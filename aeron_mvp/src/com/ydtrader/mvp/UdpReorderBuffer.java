package com.ydtrader.mvp;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.TreeMap;

/** One adapter/batch only. Preserve payloads and fail closed on unresolved gaps. */
final class UdpReorderBuffer
{
    private static final class Entry
    {
        final byte[] bytes;
        final long arrived;
        Entry(final ByteBuffer packet, final long now)
        {
            bytes = new byte[packet.remaining()];
            packet.duplicate().get(bytes);
            arrived = now;
        }
    }

    private final String adapter;
    private final long waitNs;
    private final int capacity;
    private final TreeMap<Long, Entry> pending = new TreeMap<>();
    private long expected = 1;
    private boolean waiting;
    private long reordered;
    private long recovered;

    UdpReorderBuffer(final String adapter, final long waitMs, final int capacity)
    {
        if (waitMs < 1 || waitMs > 60_000 || capacity < 1 || capacity > 65_536)
        {
            throw new IllegalArgumentException("invalid reorder bounds");
        }
        this.adapter = adapter;
        this.waitNs = waitMs * 1_000_000;
        this.capacity = capacity;
    }

    void checkTimeout(final long now)
    {
        if (!pending.isEmpty() && !pending.containsKey(expected))
        {
            for (final Entry entry : pending.values())
            {
                if (now - entry.arrived >= waitNs)
                {
                    throw failure(pending.firstKey(), "reorder_timeout");
                }
            }
        }
    }

    void offer(final long sequence, final ByteBuffer packet, final long now)
    {
        checkTimeout(now);
        if (sequence < expected || pending.containsKey(sequence))
        {
            throw failure(sequence, "duplicate_or_stale");
        }
        if (sequence > expected)
        {
            if (pending.size() >= capacity)
            {
                throw failure(sequence, "reorder_capacity");
            }
            if (!waiting)
            {
                System.out.printf("AERON_MVP_ADAPTER_PUBLISHER state=REORDER_WAIT adapter=%s " +
                    "next_sequence=%d received_sequence=%d%n", adapter, expected, sequence);
            }
            waiting = true;
            reordered++;
        }
        pending.put(sequence, new Entry(packet, now));
    }

    ByteBuffer poll(final long now)
    {
        checkTimeout(now);
        final Entry entry = pending.remove(expected);
        if (entry == null)
        {
            return null;
        }
        expected++;
        if (waiting && pending.isEmpty())
        {
            recovered++;
            waiting = false;
            System.out.printf("AERON_MVP_ADAPTER_PUBLISHER state=REORDER_RECOVERED adapter=%s " +
                "next_sequence=%d recovered_gaps=%d%n", adapter, expected, recovered);
        }
        return ByteBuffer.wrap(entry.bytes).order(ByteOrder.BIG_ENDIAN);
    }

    void logCounters()
    {
        System.out.printf("AERON_MVP_ADAPTER_PUBLISHER state=REORDER_COUNTERS adapter=%s " +
            "reorder_buffered=%d reordered_packets=%d recovered_gaps=%d%n",
            adapter, pending.size(), reordered, recovered);
    }

    private IllegalStateException failure(final long actual, final String reason)
    {
        return new IllegalStateException(adapter + " bridge sequence discontinuity expected=" +
            expected + " actual=" + actual + " reason=" + reason + " buffered=" + pending.size() +
            " wait_ms=" + waitNs / 1_000_000 + " capacity=" + capacity);
    }
}
