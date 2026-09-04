package com.ydtrader.mvp;

import com.ydtrader.mvp.codec.MarketQuoteDecoder;
import com.ydtrader.mvp.codec.MessageHeaderDecoder;
import io.aeron.Aeron;
import io.aeron.Subscription;
import io.aeron.archive.client.AeronArchive;
import io.aeron.logbuffer.Header;
import org.agrona.DirectBuffer;
import org.zeromq.SocketType;
import org.zeromq.ZContext;
import org.zeromq.ZMQ;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Bridges canonical Aeron SBE frames to an independent downstream process over ZMTP/TCP.
 * The frame bytes are forwarded unchanged; the consumer owns its decoder and queue adapter.
 */
public final class ZmqMarketDataEgress
{
    private static final String RAW_CHANNEL = "aeron:ipc?alias=ydtrader-raw";
    private static final int RAW_STREAM_ID = 1001;
    private static final int DEFAULT_REPLAY_STREAM_ID = 1104;
    private static final int FRAGMENT_LIMIT = 1024;
    private static final byte[] SNAPSHOT_TOPIC = "snapshot".getBytes(StandardCharsets.UTF_8);
    private static final String ARCHIVE_CONTROL_CHANNEL = "aeron:udp?endpoint=localhost:8010";
    private static final String ARCHIVE_RESPONSE_CHANNEL = "aeron:udp?endpoint=localhost:0";

    private ZmqMarketDataEgress()
    {
    }

    public static void main(final String[] args)
    {
        if (args.length == 0 || "help".equals(args[0]) || "--help".equals(args[0]))
        {
            usage();
            return;
        }
        try
        {
            run(Options.parse(args));
        }
        catch (final Exception ex)
        {
            System.err.printf(
                Locale.ROOT,
                "ZMQ_MARKET_EGRESS result=FAILED type=%s message=%s%n",
                ex.getClass().getSimpleName(),
                ex.getMessage());
            ex.printStackTrace(System.err);
            System.exit(1);
        }
    }

    private static void run(final Options options) throws Exception
    {
        final String mode = options.required("mode");
        if (!"live".equals(mode) && !"replay".equals(mode))
        {
            throw new IllegalArgumentException("--mode must be live or replay");
        }
        final String aeronDir = options.required("aeron-dir");
        final String endpoint = options.value("endpoint", "tcp://127.0.0.1:7101");
        if (!endpoint.startsWith("tcp://"))
        {
            throw new IllegalArgumentException("ZMQ endpoint must use tcp://: " + endpoint);
        }
        final long expectedCount = options.longValue("expected-count", 0, 0, Long.MAX_VALUE);
        final long timeoutSeconds = options.longValue("timeout-seconds", 60, 1, 86_400);
        final int sendHwm = options.integer("send-hwm", 100_000, 1, Integer.MAX_VALUE);
        final int sendTimeoutMs = options.integer("send-timeout-ms", 1_000, 1, 60_000);
        final int checkpointInterval = options.integer(
            "checkpoint-interval", 1_000, 1, Integer.MAX_VALUE);
        final Path checkpointFile = options.optionalPath("checkpoint-file");
        final Path readyFile = options.optionalPath("ready-file");

        try (ZContext context = new ZContext())
        {
            final ZMQ.Socket socket = context.createSocket(SocketType.PUB);
            socket.setLinger(0);
            socket.setSndHWM(sendHwm);
            socket.setSendTimeOut(sendTimeoutMs);
            socket.setImmediate(true);
            if (!socket.bind(endpoint))
            {
                throw new IllegalStateException("cannot bind market-data ZMQ endpoint: " + endpoint);
            }

            final Forwarder forwarder = new Forwarder(socket, checkpointFile, checkpointInterval);
            System.out.printf(
                Locale.ROOT,
                "ZMQ_MARKET_EGRESS state=STARTING mode=%s endpoint=%s expected=%d%n",
                mode,
                endpoint,
                expectedCount);
            System.out.flush();
            if ("live".equals(mode))
            {
                runLive(aeronDir, expectedCount, timeoutSeconds, readyFile, forwarder);
            }
            else
            {
                runReplay(aeronDir, expectedCount, timeoutSeconds, options, forwarder);
            }
            forwarder.writeCheckpoint();
            System.out.printf(
                Locale.ROOT,
                "ZMQ_MARKET_EGRESS result=SUCCESS mode=%s sent=%d last_sequence=%d " +
                    "last_position=%d%n",
                mode,
                forwarder.sent,
                forwarder.lastSequence,
                forwarder.lastPosition);
        }
    }

    private static void runLive(
        final String aeronDir,
        final long expectedCount,
        final long timeoutSeconds,
        final Path readyFile,
        final Forwarder forwarder) throws IOException
    {
        try (Aeron aeron = Aeron.connect(new Aeron.Context().aeronDirectoryName(aeronDir));
            Subscription subscription = aeron.addSubscription(RAW_CHANNEL, RAW_STREAM_ID))
        {
            awaitSubscriptionConnected(subscription, Duration.ofSeconds(timeoutSeconds));
            writeReadyFile(readyFile);
            pollUntilComplete(subscription, expectedCount, timeoutSeconds, forwarder);
        }
    }

    private static void runReplay(
        final String aeronDir,
        final long expectedCount,
        final long timeoutSeconds,
        final Options options,
        final Forwarder forwarder) throws IOException
    {
        final long recordingId = options.longValue(
            "recording-id", -1, 0, Long.MAX_VALUE);
        final int replayStreamId = options.integer(
            "replay-stream-id", DEFAULT_REPLAY_STREAM_ID, 1, Integer.MAX_VALUE);
        try (Aeron aeron = Aeron.connect(new Aeron.Context().aeronDirectoryName(aeronDir));
            AeronArchive archive = AeronArchive.connect(
                new AeronArchive.Context()
                    .aeron(aeron)
                    .controlRequestChannel(ARCHIVE_CONTROL_CHANNEL)
                    .controlResponseChannel(ARCHIVE_RESPONSE_CHANNEL)))
        {
            long startPosition = options.longValue("start-position", -1, -1, Long.MAX_VALUE);
            if (options.flag("resume") && forwarder.checkpointFile != null &&
                Files.isRegularFile(forwarder.checkpointFile))
            {
                startPosition = readCheckpointPosition(forwarder.checkpointFile, recordingId);
            }
            if (startPosition < 0)
            {
                startPosition = archive.getStartPosition(recordingId);
            }
            if (startPosition == AeronArchive.NULL_POSITION)
            {
                throw new IllegalStateException("recording does not exist: " + recordingId);
            }
            forwarder.recordingId = recordingId;
            try (Subscription subscription = archive.replay(
                recordingId,
                startPosition,
                AeronArchive.NULL_LENGTH,
                "aeron:ipc",
                replayStreamId))
            {
                pollUntilComplete(subscription, expectedCount, timeoutSeconds, forwarder);
            }
        }
    }

    private static void pollUntilComplete(
        final Subscription subscription,
        final long expectedCount,
        final long timeoutSeconds,
        final Forwarder forwarder)
    {
        long lastProgressNs = System.nanoTime();
        final long timeoutNs = Duration.ofSeconds(timeoutSeconds).toNanos();
        while (expectedCount == 0 || forwarder.sent < expectedCount)
        {
            final long before = forwarder.sent;
            final int fragments = subscription.poll(forwarder::onFragment, FRAGMENT_LIMIT);
            forwarder.throwIfFailed();
            if (forwarder.sent != before)
            {
                lastProgressNs = System.nanoTime();
            }
            else if (fragments == 0)
            {
                if (System.nanoTime() - lastProgressNs > timeoutNs)
                {
                    throw new IllegalStateException(
                        "no Aeron market data received for " + timeoutSeconds + " seconds");
                }
                Thread.onSpinWait();
            }
        }
    }

    private static void awaitSubscriptionConnected(
        final Subscription subscription,
        final Duration timeout)
    {
        final long deadlineNs = System.nanoTime() + timeout.toNanos();
        while (!subscription.isConnected())
        {
            if (System.nanoTime() >= deadlineNs)
            {
                throw new IllegalStateException("Aeron market-data subscription did not connect");
            }
            Thread.onSpinWait();
        }
    }

    private static void writeReadyFile(final Path readyFile) throws IOException
    {
        if (readyFile == null)
        {
            return;
        }
        final Path absolute = readyFile.toAbsolutePath();
        final Path parent = absolute.getParent();
        if (parent != null)
        {
            Files.createDirectories(parent);
        }
        Files.writeString(absolute, "READY\n", StandardCharsets.UTF_8);
    }

    private static long readCheckpointPosition(final Path path, final long recordingId)
        throws IOException
    {
        long checkpointRecordingId = Long.MIN_VALUE;
        long position = -1;
        for (String line : Files.readAllLines(path, StandardCharsets.UTF_8))
        {
            final int separator = line.indexOf('=');
            if (separator < 1)
            {
                continue;
            }
            final String key = line.substring(0, separator);
            final String value = line.substring(separator + 1);
            if ("recording_id".equals(key))
            {
                checkpointRecordingId = Long.parseLong(value);
            }
            else if ("position".equals(key))
            {
                position = Long.parseLong(value);
            }
        }
        if (checkpointRecordingId != recordingId || position < 0)
        {
            throw new IllegalStateException(
                "checkpoint does not match recording " + recordingId + ": " + path);
        }
        return position;
    }

    private static void usage()
    {
        System.out.println(
            "zmq-egress --mode live|replay --aeron-dir DIR [--recording-id ID] " +
                "[--endpoint tcp://127.0.0.1:7101] [--expected-count N] [--ready-file FILE] " +
                "[--checkpoint-file FILE] [--resume]");
    }

    private static final class Forwarder
    {
        private final ZMQ.Socket socket;
        private final Path checkpointFile;
        private final int checkpointInterval;
        private final MessageHeaderDecoder headerDecoder = new MessageHeaderDecoder();
        private final MarketQuoteDecoder quoteDecoder = new MarketQuoteDecoder();
        private String sessionId = "";
        private long expectedSequence;
        private long sent;
        private long lastSequence;
        private long lastPosition;
        private long recordingId = -1;
        private RuntimeException failure;

        private Forwarder(
            final ZMQ.Socket socket,
            final Path checkpointFile,
            final int checkpointInterval)
        {
            this.socket = socket;
            this.checkpointFile = checkpointFile;
            this.checkpointInterval = checkpointInterval;
        }

        private void onFragment(
            final DirectBuffer buffer,
            final int offset,
            final int length,
            final Header header)
        {
            if (failure != null)
            {
                return;
            }
            try
            {
                forwardFragment(buffer, offset, length, header);
            }
            catch (final RuntimeException ex)
            {
                failure = ex;
            }
        }

        private void forwardFragment(
            final DirectBuffer buffer,
            final int offset,
            final int length,
            final Header header)
        {
            headerDecoder.wrap(buffer, offset);
            if (headerDecoder.schemaId() != MarketQuoteDecoder.SCHEMA_ID ||
                headerDecoder.templateId() != MarketQuoteDecoder.TEMPLATE_ID)
            {
                throw new IllegalStateException(
                    "unexpected SBE message schema=" + headerDecoder.schemaId() +
                        " template=" + headerDecoder.templateId());
            }
            quoteDecoder.wrapAndApplyHeader(buffer, offset, headerDecoder);
            final long sequence = quoteDecoder.sequence();
            final String nextSessionId = quoteDecoder.sessionId();
            if (!nextSessionId.equals(sessionId))
            {
                sessionId = nextSessionId;
                expectedSequence = sequence;
            }
            if (sequence != expectedSequence)
            {
                throw new IllegalStateException(
                    "Aeron sequence discontinuity session=" + sessionId +
                        " expected=" + expectedSequence + " actual=" + sequence);
            }

            final byte[] payload = new byte[length];
            buffer.getBytes(offset, payload);
            if (!socket.send(SNAPSHOT_TOPIC, ZMQ.SNDMORE) || !socket.send(payload, 0))
            {
                throw new IllegalStateException(
                    "market-data ZMQ send timed out at sequence " + sequence);
            }
            expectedSequence = sequence + 1;
            lastSequence = sequence;
            lastPosition = header.position();
            sent++;
            if (checkpointFile != null && sent % checkpointInterval == 0)
            {
                try
                {
                    writeCheckpoint();
                }
                catch (final IOException ex)
                {
                    throw new IllegalStateException("cannot write checkpoint", ex);
                }
            }
        }

        private void throwIfFailed()
        {
            if (failure != null)
            {
                throw failure;
            }
        }

        private void writeCheckpoint() throws IOException
        {
            if (checkpointFile == null || lastPosition <= 0)
            {
                return;
            }
            final Path absolute = checkpointFile.toAbsolutePath();
            final Path parent = absolute.getParent();
            if (parent != null)
            {
                Files.createDirectories(parent);
            }
            final Path temporary = absolute.resolveSibling(absolute.getFileName() + ".tmp");
            Files.writeString(
                temporary,
                "recording_id=" + recordingId + "\n" +
                    "position=" + lastPosition + "\n" +
                    "session_id=" + sessionId + "\n" +
                    "sequence=" + lastSequence + "\n",
                StandardCharsets.UTF_8);
            try
            {
                Files.move(
                    temporary,
                    absolute,
                    StandardCopyOption.ATOMIC_MOVE,
                    StandardCopyOption.REPLACE_EXISTING);
            }
            catch (final AtomicMoveNotSupportedException ignored)
            {
                Files.move(temporary, absolute, StandardCopyOption.REPLACE_EXISTING);
            }
        }
    }

    private static final class Options
    {
        private final Map<String, String> values = new HashMap<>();
        private final Map<String, Boolean> flags = new HashMap<>();

        private static Options parse(final String[] args)
        {
            final Options options = new Options();
            for (int index = 0; index < args.length; index++)
            {
                final String token = args[index];
                if (!token.startsWith("--"))
                {
                    throw new IllegalArgumentException("unexpected argument: " + token);
                }
                final String key = token.substring(2);
                if ("resume".equals(key))
                {
                    options.flags.put(key, true);
                }
                else
                {
                    if (++index >= args.length)
                    {
                        throw new IllegalArgumentException("missing value for --" + key);
                    }
                    options.values.put(key, args[index]);
                }
            }
            return options;
        }

        private String required(final String key)
        {
            final String value = values.get(key);
            if (value == null || value.isBlank())
            {
                throw new IllegalArgumentException("missing required option --" + key);
            }
            return value;
        }

        private String value(final String key, final String fallback)
        {
            return values.getOrDefault(key, fallback);
        }

        private boolean flag(final String key)
        {
            return flags.getOrDefault(key, false);
        }

        private int integer(final String key, final int fallback, final int minimum, final int maximum)
        {
            return Math.toIntExact(longValue(key, fallback, minimum, maximum));
        }

        private long longValue(
            final String key,
            final long fallback,
            final long minimum,
            final long maximum)
        {
            final String raw = values.get(key);
            final long value = raw == null ? fallback : Long.parseLong(raw);
            if (value < minimum || value > maximum)
            {
                throw new IllegalArgumentException(
                    "--" + key + " must be between " + minimum + " and " + maximum);
            }
            return value;
        }

        private Path optionalPath(final String key)
        {
            final String raw = values.get(key);
            return raw == null ? null : Path.of(raw);
        }
    }
}
