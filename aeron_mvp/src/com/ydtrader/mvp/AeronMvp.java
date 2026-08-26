package com.ydtrader.mvp;

import com.ydtrader.mvp.codec.BooleanType;
import com.ydtrader.mvp.codec.MarketQuoteDecoder;
import com.ydtrader.mvp.codec.MarketQuoteEncoder;
import com.ydtrader.mvp.codec.MessageHeaderDecoder;
import com.ydtrader.mvp.codec.MessageHeaderEncoder;
import io.aeron.Aeron;
import io.aeron.ExclusivePublication;
import io.aeron.Publication;
import io.aeron.Subscription;
import io.aeron.archive.Archive;
import io.aeron.archive.ArchivingMediaDriver;
import io.aeron.archive.client.AeronArchive;
import io.aeron.archive.codecs.SourceLocation;
import io.aeron.archive.status.RecordingPos;
import io.aeron.driver.MediaDriver;
import org.agrona.concurrent.UnsafeBuffer;
import org.agrona.concurrent.status.CountersReader;

import java.io.File;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.DatagramChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public final class AeronMvp
{
    private static final String RAW_CHANNEL = "aeron:ipc?alias=ydtrader-raw";
    private static final String ARCHIVE_CONTROL_CHANNEL = "aeron:udp?endpoint=localhost:8010";
    private static final String ARCHIVE_LOCAL_CONTROL_CHANNEL = "aeron:ipc";
    private static final String ARCHIVE_EVENTS_CHANNEL =
        "aeron:udp?control-mode=dynamic|control=localhost:8030";
    private static final String ARCHIVE_RESPONSE_CHANNEL = "aeron:udp?endpoint=localhost:0";
    private static final String ARCHIVE_REPLICATION_CHANNEL = "aeron:udp?endpoint=localhost:0";
    private static final int RAW_STREAM_ID = 1001;
    private static final int COMPUTE_REPLAY_STREAM_ID = 1101;
    private static final int AUDIT_REPLAY_STREAM_ID = 1102;
    private static final int OFFLINE_REPLAY_STREAM_ID = 1103;
    private static final int BUFFER_CAPACITY = 2048;
    private static final int FRAGMENT_LIMIT = 1024;
    private static final long OFFER_TIMEOUT_NS = Duration.ofSeconds(15).toNanos();
    private static final int CTP_PACKET_MAGIC = 0x43545031;
    private static final int CTP_PACKET_VERSION = 1;
    private static final int CTP_PACKET_SIZE = 156;
    private static final int CTP_TIMESTAMP_VALID_FLAG = 1;
    private static final int CTP_MAX_REPEAT = 1_000_000;

    private AeronMvp()
    {
    }

    public static void main(final String[] args)
    {
        if (args.length == 0 || "help".equals(args[0]) || "--help".equals(args[0]))
        {
            usage();
            return;
        }

        final String command = args[0];
        final Options options = Options.parse(Arrays.copyOfRange(args, 1, args.length));
        try
        {
            final int exitCode;
            switch (command)
            {
                case "server":
                    exitCode = runServer(options);
                    break;
                case "publish":
                    exitCode = runPublisher(options);
                    break;
                case "publish-ctp":
                    exitCode = runCtpPublisher(options);
                    break;
                case "compute":
                    exitCode = runConsumer(options, ConsumerMode.COMPUTE);
                    break;
                case "audit":
                    exitCode = runConsumer(options, ConsumerMode.AUDIT);
                    break;
                case "selftest":
                    exitCode = runSelfTest();
                    break;
                default:
                    throw new IllegalArgumentException("unknown command: " + command);
            }
            if (exitCode != 0)
            {
                System.exit(exitCode);
            }
        }
        catch (final Exception ex)
        {
            System.err.printf(
                Locale.ROOT,
                "AERON_MVP result=FAILED command=%s type=%s message=%s%n",
                command,
                ex.getClass().getSimpleName(),
                ex.getMessage());
            ex.printStackTrace(System.err);
            System.exit(1);
        }
    }

    private static int runServer(final Options options) throws Exception
    {
        final String aeronDir = options.required("aeron-dir");
        final File archiveDir = Path.of(options.required("archive-dir")).toFile();
        final Path readyFile = Path.of(options.required("ready-file"));
        final int syncLevel = options.integer("sync-level", 0, 0, 2);
        final boolean reuseArchive = options.flag("reuse-archive");
        Files.createDirectories(archiveDir.toPath());

        final MediaDriver.Context driverContext = new MediaDriver.Context()
            .aeronDirectoryName(aeronDir)
            .spiesSimulateConnection(true)
            .dirDeleteOnStart(true)
            .dirDeleteOnShutdown(true);
        final Archive.Context archiveContext = new Archive.Context()
            .aeronDirectoryName(aeronDir)
            .archiveDir(archiveDir)
            .deleteArchiveOnStart(!reuseArchive)
            .controlChannel(ARCHIVE_CONTROL_CHANNEL)
            .localControlChannel(ARCHIVE_LOCAL_CONTROL_CHANNEL)
            .recordingEventsChannel(ARCHIVE_EVENTS_CHANNEL)
            .replicationChannel(ARCHIVE_REPLICATION_CHANNEL)
            .fileSyncLevel(syncLevel)
            .catalogFileSyncLevel(syncLevel);

        final CountDownLatch stop = new CountDownLatch(1);
        final CountDownLatch closed = new CountDownLatch(1);
        final Thread shutdownHook = new Thread(
            () ->
            {
                stop.countDown();
                try
                {
                    closed.await(10, TimeUnit.SECONDS);
                }
                catch (final InterruptedException ex)
                {
                    Thread.currentThread().interrupt();
                }
            },
            "aeron-mvp-shutdown");
        Runtime.getRuntime().addShutdownHook(shutdownHook);

        try (ArchivingMediaDriver ignored = ArchivingMediaDriver.launch(driverContext, archiveContext))
        {
            writeAtomically(readyFile, "READY\n");
            System.out.printf(
                Locale.ROOT,
                "AERON_MVP_SERVER result=READY aeron_dir=%s archive_dir=%s sync_level=%d " +
                    "reuse_archive=%s%n",
                aeronDir,
                archiveDir,
                syncLevel,
                reuseArchive ? "YES" : "NO");
            System.out.flush();
            stop.await();
        }
        finally
        {
            closed.countDown();
            try
            {
                Runtime.getRuntime().removeShutdownHook(shutdownHook);
            }
            catch (final IllegalStateException ignored)
            {
                // The JVM is already running shutdown hooks.
            }
        }
        return 0;
    }

    private static int runPublisher(final Options options) throws Exception
    {
        final String aeronDir = options.required("aeron-dir");
        final Path recordingFile = Path.of(options.required("recording-file"));
        final int count = options.integer("count", 100_000, 1, Integer.MAX_VALUE);
        final long warmupMs = options.longValue("warmup-ms", 3_000L, 0L, 60_000L);
        final String instrument = options.value("instrument", "IC2609");
        final String sessionId = UUID.randomUUID().toString().replace("-", "");
        final UnsafeBuffer buffer = new UnsafeBuffer(ByteBuffer.allocateDirect(BUFFER_CAPACITY));
        final MessageHeaderEncoder headerEncoder = new MessageHeaderEncoder();
        final MarketQuoteEncoder quoteEncoder = new MarketQuoteEncoder();

        long backPressureCount = 0;
        long recordingId;
        long stopPosition;

        try (Aeron aeron = connectAeron(aeronDir);
            AeronArchive archive = connectArchive(aeron))
        {
            final long recordingSubscriptionId = archive.startRecording(
                RAW_CHANNEL, RAW_STREAM_ID, SourceLocation.LOCAL);
            try (ExclusivePublication publication = aeron.addExclusivePublication(
                RAW_CHANNEL, RAW_STREAM_ID))
            {
                awaitPublicationConnected(publication, Duration.ofSeconds(10));
                final CountersReader counters = aeron.countersReader();
                final int counterId = awaitRecordingCounter(
                    counters, publication.sessionId(), archive.archiveId(), Duration.ofSeconds(10));
                recordingId = RecordingPos.getRecordingId(counters, counterId);
                writeAtomically(recordingFile, Long.toString(recordingId) + "\n");
                System.out.printf(
                    Locale.ROOT,
                    "AERON_MVP_PUBLISHER state=RECORDING recording_id=%d session_id=%s count=%d%n",
                    recordingId,
                    sessionId,
                    count);
                System.out.flush();

                if (warmupMs > 0)
                {
                    Thread.sleep(warmupMs);
                }

                for (int index = 0; index < count; index++)
                {
                    final long sequence = index + 1L;
                    final long localReceiveNs =
                        System.currentTimeMillis() * 1_000_000L + index % 1_000_000L;
                    final long simulatedLatencyNs = ((index % 50L) + 1L) * 1_000_000L;
                    final long marketTimestampNs = localReceiveNs - simulatedLatencyNs;
                    final int encodedLength = encodeQuote(
                        buffer,
                        headerEncoder,
                        quoteEncoder,
                        sequence,
                        marketTimestampNs,
                        localReceiveNs,
                        sessionId,
                        instrument);
                    backPressureCount += offerUntilAccepted(publication, buffer, encodedLength);
                }

                stopPosition = publication.position();
                awaitCounterPosition(counters, counterId, stopPosition, Duration.ofSeconds(30));
            }
            archive.stopRecording(recordingSubscriptionId);
            awaitStopPosition(archive, recordingId, stopPosition, Duration.ofSeconds(10));
        }

        System.out.printf(
            Locale.ROOT,
            "AERON_MVP_PUBLISH result=SUCCESS recording_id=%d sent=%d stop_position=%d " +
                "offer_retries=%d%n",
            recordingId,
            count,
            stopPosition,
            backPressureCount);
        return 0;
    }

    private static int runCtpPublisher(final Options options) throws Exception
    {
        final String aeronDir = options.required("aeron-dir");
        final Path recordingFile = Path.of(options.required("recording-file"));
        final int count = options.integer("count", 100_000, 1, Integer.MAX_VALUE);
        final String bindHost = options.value("bind-host", "127.0.0.1");
        final int udpPort = options.integer("udp-port", 24001, 1, 65535);
        final long sourceTimeoutSeconds = options.longValue(
            "source-timeout-seconds", 60L, 1L, 3600L);
        final String sessionId = "ctp-" + UUID.randomUUID().toString().replace("-", "");
        final UnsafeBuffer buffer = new UnsafeBuffer(ByteBuffer.allocateDirect(BUFFER_CAPACITY));
        final MessageHeaderEncoder headerEncoder = new MessageHeaderEncoder();
        final MarketQuoteEncoder quoteEncoder = new MarketQuoteEncoder();
        final ByteBuffer packet = ByteBuffer.allocateDirect(CTP_PACKET_SIZE).order(ByteOrder.BIG_ENDIAN);

        long backPressureCount = 0;
        long sourceTicks = 0;
        long published = 0;
        long lastSourceSequence = 0;
        long recordingId;
        long stopPosition;

        try (DatagramChannel source = DatagramChannel.open();
            Aeron aeron = connectAeron(aeronDir);
            AeronArchive archive = connectArchive(aeron))
        {
            source.bind(new InetSocketAddress(bindHost, udpPort));
            source.configureBlocking(false);
            final long recordingSubscriptionId = archive.startRecording(
                RAW_CHANNEL, RAW_STREAM_ID, SourceLocation.LOCAL);
            try (ExclusivePublication publication = aeron.addExclusivePublication(
                RAW_CHANNEL, RAW_STREAM_ID))
            {
                awaitPublicationConnected(publication, Duration.ofSeconds(10));
                final CountersReader counters = aeron.countersReader();
                final int counterId = awaitRecordingCounter(
                    counters, publication.sessionId(), archive.archiveId(), Duration.ofSeconds(10));
                recordingId = RecordingPos.getRecordingId(counters, counterId);
                writeAtomically(recordingFile, Long.toString(recordingId) + "\n");
                System.out.printf(
                    Locale.ROOT,
                    "AERON_MVP_CTP_PUBLISHER state=WAITING recording_id=%d session_id=%s " +
                        "bind=%s:%d count=%d%n",
                    recordingId,
                    sessionId,
                    bindHost,
                    udpPort,
                    count);
                System.out.flush();

                long lastPacketNs = System.nanoTime();
                while (published < count)
                {
                    packet.clear();
                    if (source.receive(packet) == null)
                    {
                        if (System.nanoTime() - lastPacketNs >
                            Duration.ofSeconds(sourceTimeoutSeconds).toNanos())
                        {
                            throw new IllegalStateException(
                                "no valid CTP packet received for " + sourceTimeoutSeconds + " seconds");
                        }
                        Thread.onSpinWait();
                        continue;
                    }
                    lastPacketNs = System.nanoTime();
                    packet.flip();
                    if (packet.remaining() != CTP_PACKET_SIZE)
                    {
                        throw new IllegalArgumentException(
                            "unexpected CTP packet size: " + packet.remaining());
                    }
                    final int magic = packet.getInt();
                    final int version = Short.toUnsignedInt(packet.getShort());
                    final int flags = Short.toUnsignedInt(packet.getShort());
                    final int repeat = packet.getInt();
                    final long sourceSequence = packet.getLong();
                    final long marketTimestampNs = packet.getLong();
                    final long localReceiveNs = packet.getLong();
                    final double lastPrice = packet.getDouble();
                    final double bidPrice = packet.getDouble();
                    final double askPrice = packet.getDouble();
                    final long bidVolume = packet.getLong();
                    final long askVolume = packet.getLong();
                    final String instrument = readFixedUtf8(packet, 32);
                    final String tradingDay = readFixedUtf8(packet, 16);
                    final String marketTimestampRaw = readFixedUtf8(packet, 32);

                    if (magic != CTP_PACKET_MAGIC || version != CTP_PACKET_VERSION)
                    {
                        throw new IllegalArgumentException(
                            "invalid CTP packet header magic=" + magic + " version=" + version);
                    }
                    if (repeat < 1 || repeat > CTP_MAX_REPEAT)
                    {
                        throw new IllegalArgumentException("invalid CTP repeat: " + repeat);
                    }
                    if (sourceSequence != lastSourceSequence + 1)
                    {
                        throw new IllegalStateException(
                            "CTP bridge sequence discontinuity expected=" +
                                (lastSourceSequence + 1) + " actual=" + sourceSequence);
                    }
                    if (instrument.isEmpty())
                    {
                        throw new IllegalArgumentException("CTP packet instrument is empty");
                    }
                    lastSourceSequence = sourceSequence;
                    sourceTicks++;

                    final int expanded = (int)Math.min((long)repeat, count - published);
                    for (int index = 0; index < expanded; index++)
                    {
                        final long sequence = published + 1;
                        final int encodedLength = encodeQuote(
                            buffer,
                            headerEncoder,
                            quoteEncoder,
                            sequence,
                            marketTimestampNs,
                            localReceiveNs,
                            lastPrice,
                            bidPrice,
                            askPrice,
                            bidVolume,
                            askVolume,
                            (flags & CTP_TIMESTAMP_VALID_FLAG) != 0,
                            sessionId,
                            instrument,
                            tradingDay,
                            marketTimestampRaw);
                        backPressureCount += offerUntilAccepted(publication, buffer, encodedLength);
                        published++;
                    }
                }

                stopPosition = publication.position();
                awaitCounterPosition(counters, counterId, stopPosition, Duration.ofSeconds(30));
            }
            archive.stopRecording(recordingSubscriptionId);
            awaitStopPosition(archive, recordingId, stopPosition, Duration.ofSeconds(10));
        }

        System.out.printf(
            Locale.ROOT,
            "AERON_MVP_CTP_PUBLISH result=SUCCESS recording_id=%d source_ticks=%d " +
                "published=%d stop_position=%d offer_retries=%d%n",
            recordingId,
            sourceTicks,
            published,
            stopPosition,
            backPressureCount);
        return 0;
    }

    private static int runConsumer(final Options options, final ConsumerMode mode) throws Exception
    {
        final String aeronDir = options.required("aeron-dir");
        final long recordingId = options.longValue("recording-id", -1, 0, Long.MAX_VALUE);
        final int expectedCount = options.integer("expected-count", -1, 1, Integer.MAX_VALUE);
        final long timeoutSeconds = options.longValue("timeout-seconds", 30, 1, 3600);
        final Path summaryFile = options.optionalPath("summary-file");
        final int defaultReplayStreamId;
        if (mode == ConsumerMode.AUDIT)
        {
            defaultReplayStreamId = AUDIT_REPLAY_STREAM_ID;
        }
        else if (options.flag("offline"))
        {
            defaultReplayStreamId = OFFLINE_REPLAY_STREAM_ID;
        }
        else
        {
            defaultReplayStreamId = COMPUTE_REPLAY_STREAM_ID;
        }
        final int replayStreamId = options.integer(
            "replay-stream-id", defaultReplayStreamId, 1, Integer.MAX_VALUE);

        final ConsumerState state = new ConsumerState(expectedCount, mode);
        final long startedNs = System.nanoTime();
        try (Aeron aeron = connectAeron(aeronDir);
            AeronArchive archive = connectArchive(aeron))
        {
            final long startPosition = archive.getStartPosition(recordingId);
            if (startPosition == AeronArchive.NULL_POSITION)
            {
                throw new IllegalStateException("recording does not exist: " + recordingId);
            }
            try (Subscription subscription = archive.replay(
                recordingId,
                startPosition,
                AeronArchive.NULL_LENGTH,
                "aeron:ipc",
                replayStreamId))
            {
                System.out.printf(
                    Locale.ROOT,
                    "AERON_MVP_%s state=REPLAY_CONNECTED recording_id=%d " +
                        "replay_stream_id=%d expected=%d%n",
                    mode,
                    recordingId,
                    replayStreamId,
                    expectedCount);
                System.out.flush();
                long lastProgressNs = System.nanoTime();
                while (state.received < expectedCount)
                {
                    final long before = state.received;
                    final int fragments = subscription.poll(state::onFragment, FRAGMENT_LIMIT);
                    if (state.received != before)
                    {
                        lastProgressNs = System.nanoTime();
                    }
                    if (fragments == 0)
                    {
                        if (System.nanoTime() - lastProgressNs > Duration.ofSeconds(timeoutSeconds).toNanos())
                        {
                            throw new IllegalStateException(
                                "replay made no progress for " + timeoutSeconds + " seconds");
                        }
                        Thread.onSpinWait();
                    }
                }
            }
        }

        final long elapsedNs = System.nanoTime() - startedNs;
        final Summary summary = state.summary(recordingId, elapsedNs);
        final String output = summary.serialize();
        System.out.print(output);
        if (summaryFile != null)
        {
            writeAtomically(summaryFile, summary.serializeStable());
        }
        return summary.complete ? 0 : 4;
    }

    private static int runSelfTest()
    {
        final UnsafeBuffer buffer = new UnsafeBuffer(ByteBuffer.allocateDirect(BUFFER_CAPACITY));
        final MessageHeaderEncoder headerEncoder = new MessageHeaderEncoder();
        final MarketQuoteEncoder quoteEncoder = new MarketQuoteEncoder();
        final int encodedLength = encodeQuote(
            buffer,
            headerEncoder,
            quoteEncoder,
            7,
            1_000_000_000L,
            1_012_000_000L,
            "selftest-session",
            "IC2609");
        final MessageHeaderDecoder headerDecoder = new MessageHeaderDecoder();
        final MarketQuoteDecoder quoteDecoder = new MarketQuoteDecoder();
        quoteDecoder.wrapAndApplyHeader(buffer, 0, headerDecoder);
        if (encodedLength <= MessageHeaderEncoder.ENCODED_LENGTH ||
            quoteDecoder.sequence() != 7 ||
            quoteDecoder.localReceiveNs() - quoteDecoder.marketTimestampNs() != 12_000_000L ||
            !"selftest-session".equals(quoteDecoder.sessionId()))
        {
            throw new IllegalStateException("SBE market quote round-trip failed");
        }
        System.out.printf(
            Locale.ROOT,
            "AERON_MVP_SELFTEST result=SUCCESS encoded_length=%d schema_id=%d template_id=%d%n",
            encodedLength,
            MarketQuoteDecoder.SCHEMA_ID,
            MarketQuoteDecoder.TEMPLATE_ID);
        return 0;
    }

    private static int encodeQuote(
        final UnsafeBuffer buffer,
        final MessageHeaderEncoder headerEncoder,
        final MarketQuoteEncoder encoder,
        final long sequence,
        final long marketTimestampNs,
        final long localReceiveNs,
        final String sessionId,
        final String instrument)
    {
        return encodeQuote(
            buffer,
            headerEncoder,
            encoder,
            sequence,
            marketTimestampNs,
            localReceiveNs,
            5_000.0 + sequence * 0.01,
            4_999.8 + sequence * 0.01,
            5_000.2 + sequence * 0.01,
            10 + sequence % 100,
            20 + sequence % 100,
            true,
            sessionId,
            instrument,
            "20260826",
            Long.toString(marketTimestampNs));
    }

    private static int encodeQuote(
        final UnsafeBuffer buffer,
        final MessageHeaderEncoder headerEncoder,
        final MarketQuoteEncoder encoder,
        final long sequence,
        final long marketTimestampNs,
        final long localReceiveNs,
        final double lastPrice,
        final double bidPrice,
        final double askPrice,
        final long bidVolume,
        final long askVolume,
        final boolean timestampValid,
        final String sessionId,
        final String instrument,
        final String tradingDay,
        final String marketTimestampRaw)
    {
        encoder.wrapAndApplyHeader(buffer, 0, headerEncoder)
            .sequence(sequence)
            .marketTimestampNs(marketTimestampNs)
            .localReceiveNs(localReceiveNs)
            .lastPrice(lastPrice)
            .bidPrice(bidPrice)
            .askPrice(askPrice)
            .bidVolume(bidVolume)
            .askVolume(askVolume)
            .timestampValid(timestampValid ? BooleanType.TRUE : BooleanType.FALSE)
            .sessionId(sessionId)
            .instrument(instrument)
            .tradingDay(tradingDay)
            .marketTimestampRaw(marketTimestampRaw);
        return MessageHeaderEncoder.ENCODED_LENGTH + encoder.encodedLength();
    }

    private static String readFixedUtf8(final ByteBuffer packet, final int width)
    {
        final byte[] bytes = new byte[width];
        packet.get(bytes);
        int length = 0;
        while (length < bytes.length && bytes[length] != 0)
        {
            length++;
        }
        return new String(bytes, 0, length, StandardCharsets.UTF_8);
    }

    private static long offerUntilAccepted(
        final ExclusivePublication publication,
        final UnsafeBuffer buffer,
        final int encodedLength)
    {
        final long deadlineNs = System.nanoTime() + OFFER_TIMEOUT_NS;
        long retries = 0;
        while (true)
        {
            final long result = publication.offer(buffer, 0, encodedLength);
            if (result >= 0)
            {
                return retries;
            }
            retries++;
            if (result == Publication.CLOSED)
            {
                throw new IllegalStateException("publication closed while offering quote");
            }
            if (result == Publication.MAX_POSITION_EXCEEDED)
            {
                throw new IllegalStateException("publication reached max position");
            }
            if (System.nanoTime() >= deadlineNs)
            {
                throw new IllegalStateException(
                    "Aeron offer remained unavailable; last_status=" + result);
            }
            Thread.onSpinWait();
        }
    }

    private static Aeron connectAeron(final String aeronDir)
    {
        return Aeron.connect(new Aeron.Context().aeronDirectoryName(aeronDir));
    }

    private static AeronArchive connectArchive(final Aeron aeron)
    {
        return AeronArchive.connect(
            new AeronArchive.Context()
                .aeron(aeron)
                .controlRequestChannel(ARCHIVE_CONTROL_CHANNEL)
                .controlResponseChannel(ARCHIVE_RESPONSE_CHANNEL));
    }

    private static void awaitPublicationConnected(
        final ExclusivePublication publication,
        final Duration timeout)
    {
        final long deadlineNs = System.nanoTime() + timeout.toNanos();
        while (!publication.isConnected())
        {
            if (System.nanoTime() >= deadlineNs)
            {
                throw new IllegalStateException("publication did not connect to Archive recording");
            }
            Thread.onSpinWait();
        }
    }

    private static int awaitRecordingCounter(
        final CountersReader counters,
        final int sessionId,
        final long archiveId,
        final Duration timeout)
    {
        final long deadlineNs = System.nanoTime() + timeout.toNanos();
        int counterId;
        while ((counterId = RecordingPos.findCounterIdBySession(counters, sessionId, archiveId)) < 0)
        {
            if (System.nanoTime() >= deadlineNs)
            {
                throw new IllegalStateException("Archive did not create a recording counter");
            }
            Thread.onSpinWait();
        }
        return counterId;
    }

    private static void awaitCounterPosition(
        final CountersReader counters,
        final int counterId,
        final long targetPosition,
        final Duration timeout)
    {
        final long deadlineNs = System.nanoTime() + timeout.toNanos();
        while (counters.getCounterValue(counterId) < targetPosition)
        {
            if (System.nanoTime() >= deadlineNs)
            {
                throw new IllegalStateException(
                    "Archive recording did not reach publication position " + targetPosition);
            }
            Thread.onSpinWait();
        }
    }

    private static void awaitStopPosition(
        final AeronArchive archive,
        final long recordingId,
        final long targetPosition,
        final Duration timeout)
    {
        final long deadlineNs = System.nanoTime() + timeout.toNanos();
        while (archive.getStopPosition(recordingId) != targetPosition)
        {
            if (System.nanoTime() >= deadlineNs)
            {
                throw new IllegalStateException(
                    "Archive did not finalize stop position " + targetPosition);
            }
            Thread.onSpinWait();
        }
    }

    private static void writeAtomically(final Path path, final String contents) throws IOException
    {
        final Path absolute = path.toAbsolutePath();
        final Path parent = absolute.getParent();
        if (parent != null)
        {
            Files.createDirectories(parent);
        }
        final Path temporary = absolute.resolveSibling(absolute.getFileName() + ".tmp");
        Files.writeString(temporary, contents);
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

    private static void usage()
    {
        System.out.println("usage: AeronMvp <command> [options]");
        System.out.println(
            "  server  --aeron-dir DIR --archive-dir DIR --ready-file FILE " +
            "[--sync-level 0] [--reuse-archive]");
        System.out.println("  publish --aeron-dir DIR --recording-file FILE [--count N] [--warmup-ms N]");
        System.out.println(
            "  publish-ctp --aeron-dir DIR --recording-file FILE [--count N] " +
            "[--bind-host 127.0.0.1] [--udp-port 24001] [--source-timeout-seconds 60]");
        System.out.println("  compute --aeron-dir DIR --recording-id ID --expected-count N [--offline]");
        System.out.println("  audit   --aeron-dir DIR --recording-id ID --expected-count N");
        System.out.println("  selftest");
    }

    private enum ConsumerMode
    {
        COMPUTE,
        AUDIT
    }

    private static final class ConsumerState
    {
        private final int expectedCount;
        private final ConsumerMode mode;
        private final MessageHeaderDecoder headerDecoder = new MessageHeaderDecoder();
        private final MarketQuoteDecoder quoteDecoder = new MarketQuoteDecoder();
        private final Set<String> sessions = new HashSet<>();
        private final double[] latencyValues;
        private long received;
        private long expectedSequence = 1;
        private long gaps;
        private long duplicates;
        private long invalidTimestamps;
        private int measured;
        private double mean;
        private double m2;
        private double max = Double.NEGATIVE_INFINITY;

        private ConsumerState(final int expectedCount, final ConsumerMode mode)
        {
            this.expectedCount = expectedCount;
            this.mode = mode;
            this.latencyValues = mode == ConsumerMode.COMPUTE ? new double[expectedCount] : null;
        }

        private void onFragment(
            final org.agrona.DirectBuffer buffer,
            final int offset,
            final int length,
            final io.aeron.logbuffer.Header ignored)
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
            if (sequence > expectedSequence)
            {
                gaps += sequence - expectedSequence;
            }
            else if (sequence < expectedSequence)
            {
                duplicates++;
            }
            expectedSequence = Math.max(expectedSequence, sequence + 1);
            received++;

            if (mode == ConsumerMode.COMPUTE)
            {
                if (quoteDecoder.timestampValid() != BooleanType.TRUE)
                {
                    invalidTimestamps++;
                    return;
                }
                final long marketTimestampNs = quoteDecoder.marketTimestampNs();
                final long localReceiveNs = quoteDecoder.localReceiveNs();
                final String sessionId = quoteDecoder.sessionId();
                if (sessions.add(sessionId))
                {
                    return;
                }
                final double latencyMs = Math.abs(localReceiveNs - marketTimestampNs) / 1_000_000.0;
                latencyValues[measured] = latencyMs;
                measured++;
                final double delta = latencyMs - mean;
                mean += delta / measured;
                m2 += delta * (latencyMs - mean);
                max = Math.max(max, latencyMs);
            }

            if (received % 100_000 == 0 || received == expectedCount)
            {
                System.out.printf(
                    Locale.ROOT,
                    "AERON_MVP_%s state=RUNNING received=%d expected=%d gaps=%d duplicates=%d%n",
                    mode,
                    received,
                    expectedCount,
                    gaps,
                    duplicates);
                System.out.flush();
            }
        }

        private Summary summary(final long recordingId, final long elapsedNs)
        {
            final boolean complete =
                received == expectedCount && gaps == 0 && duplicates == 0 && invalidTimestamps == 0;
            if (mode == ConsumerMode.AUDIT)
            {
                return new Summary(
                    mode,
                    complete,
                    recordingId,
                    received,
                    0,
                    gaps,
                    duplicates,
                    invalidTimestamps,
                    0,
                    0,
                    0,
                    elapsedNs);
            }

            final double[] sorted = Arrays.copyOf(latencyValues, measured);
            Arrays.sort(sorted);
            final double p95 = measured == 0 ? 0 : sorted[(int)Math.ceil(measured * 0.95) - 1];
            final double std = measured > 1 ? Math.sqrt(m2 / (measured - 1)) : 0;
            return new Summary(
                mode,
                complete && measured == expectedCount - sessions.size(),
                recordingId,
                received,
                measured,
                gaps,
                duplicates,
                invalidTimestamps,
                mean,
                std,
                p95,
                max == Double.NEGATIVE_INFINITY ? 0 : max,
                elapsedNs);
        }
    }

    private static final class Summary
    {
        private final ConsumerMode mode;
        private final boolean complete;
        private final long recordingId;
        private final long received;
        private final int measured;
        private final long gaps;
        private final long duplicates;
        private final long invalidTimestamps;
        private final double mean;
        private final double std;
        private final double p95;
        private final double max;
        private final long elapsedNs;

        private Summary(
            final ConsumerMode mode,
            final boolean complete,
            final long recordingId,
            final long received,
            final int measured,
            final long gaps,
            final long duplicates,
            final long invalidTimestamps,
            final double mean,
            final double std,
            final double p95,
            final double max,
            final long elapsedNs)
        {
            this.mode = mode;
            this.complete = complete;
            this.recordingId = recordingId;
            this.received = received;
            this.measured = measured;
            this.gaps = gaps;
            this.duplicates = duplicates;
            this.invalidTimestamps = invalidTimestamps;
            this.mean = mean;
            this.std = std;
            this.p95 = p95;
            this.max = max;
            this.elapsedNs = elapsedNs;
        }

        private Summary(
            final ConsumerMode mode,
            final boolean complete,
            final long recordingId,
            final long received,
            final int measured,
            final long gaps,
            final long duplicates,
            final long invalidTimestamps,
            final double mean,
            final double std,
            final double p95,
            final long elapsedNs)
        {
            this(
                mode,
                complete,
                recordingId,
                received,
                measured,
                gaps,
                duplicates,
                invalidTimestamps,
                mean,
                std,
                p95,
                0,
                elapsedNs);
        }

        private String serialize()
        {
            final double seconds = elapsedNs / 1_000_000_000.0;
            final double rate = seconds == 0 ? 0 : received / seconds;
            return String.format(
                Locale.ROOT,
                "AERON_MVP_SUMMARY mode=%s status=%s recording_id=%d received=%d measured=%d " +
                    "gaps=%d duplicates=%d invalid_timestamps=%d mean_ms=%.6f std_ms=%.6f " +
                    "p95_ms=%.3f max_ms=%.3f elapsed_seconds=%.6f rate_per_second=%.3f%n",
                mode,
                complete ? "SUCCESS" : "INCOMPLETE",
                recordingId,
                received,
                measured,
                gaps,
                duplicates,
                invalidTimestamps,
                mean,
                std,
                p95,
                max,
                seconds,
                rate);
        }

        private String serializeStable()
        {
            return String.format(
                Locale.ROOT,
                "mode=%s%nstatus=%s%nrecording_id=%d%nreceived=%d%nmeasured=%d%n" +
                    "gaps=%d%nduplicates=%d%ninvalid_timestamps=%d%nmean_ms=%.6f%n" +
                    "std_ms=%.6f%np95_ms=%.3f%nmax_ms=%.3f%n",
                mode,
                complete ? "SUCCESS" : "INCOMPLETE",
                recordingId,
                received,
                measured,
                gaps,
                duplicates,
                invalidTimestamps,
                mean,
                std,
                p95,
                max);
        }
    }

    private static final class Options
    {
        private final Map<String, String> values;
        private final Set<String> flags;

        private Options(final Map<String, String> values, final Set<String> flags)
        {
            this.values = values;
            this.flags = flags;
        }

        private static Options parse(final String[] args)
        {
            final Map<String, String> values = new HashMap<>();
            final Set<String> flags = new HashSet<>();
            final List<String> tokens = new ArrayList<>(Arrays.asList(args));
            for (int index = 0; index < tokens.size(); index++)
            {
                final String token = tokens.get(index);
                if (!token.startsWith("--"))
                {
                    throw new IllegalArgumentException("unexpected argument: " + token);
                }
                final String key = token.substring(2);
                if ("offline".equals(key) || "reuse-archive".equals(key))
                {
                    flags.add(key);
                    continue;
                }
                if (index + 1 >= tokens.size())
                {
                    throw new IllegalArgumentException("missing value for --" + key);
                }
                values.put(key, tokens.get(++index));
            }
            return new Options(values, flags);
        }

        private String required(final String key)
        {
            final String result = values.get(key);
            if (result == null || result.isBlank())
            {
                throw new IllegalArgumentException("missing required option --" + key);
            }
            return result;
        }

        private String value(final String key, final String fallback)
        {
            return values.getOrDefault(key, fallback);
        }

        private boolean flag(final String key)
        {
            return flags.contains(key);
        }

        private int integer(final String key, final int fallback, final int minimum, final int maximum)
        {
            final long result = longValue(key, fallback, minimum, maximum);
            return Math.toIntExact(result);
        }

        private long longValue(
            final String key,
            final long fallback,
            final long minimum,
            final long maximum)
        {
            final String raw = values.get(key);
            final long result = raw == null ? fallback : Long.parseLong(raw);
            if (result < minimum || result > maximum)
            {
                throw new IllegalArgumentException(
                    "--" + key + " must be between " + minimum + " and " + maximum);
            }
            return result;
        }

        private Path optionalPath(final String key)
        {
            final String raw = values.get(key);
            return raw == null ? null : Path.of(raw);
        }
    }
}
