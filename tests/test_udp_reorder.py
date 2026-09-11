"""Deterministic replay of the captured UDP reorder; no live services needed."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from aeron_mvp.multi_source_mux import PacketReorderBuffer


def test_captured_order_and_payload_preserved(capsys):
    buffer = PacketReorderBuffer("ctp-live", expected=5485)
    result = []
    for i, sequence in enumerate([5485, 5487, 5486, 5488]):
        result.extend(buffer.offer(sequence, str(sequence).encode(), i * .00002))
    assert result == [b"5485", b"5486", b"5487", b"5488"]
    assert buffer.reordered_packets == buffer.recovered_gaps == 1
    assert not buffer.pending
    assert "expected=" not in capsys.readouterr().out


def test_sources_independent_and_ordered_path_immediate():
    a, b = PacketReorderBuffer("a"), PacketReorderBuffer("b")
    assert a.offer(2, b"two", 0) == []
    assert b.offer(1, b"one", 0) == [b"one"]
    assert a.offer(1, b"one", .001) == [b"one", b"two"]


def test_gap_timeout_not_extended():
    buffer = PacketReorderBuffer()
    buffer.offer(3, b"three", 0)
    buffer.offer(4, b"four", .09)
    assert buffer.offer(1, b"one", .095) == [b"one"]
    with pytest.raises(RuntimeError, match="expected=2 actual=3 reason=reorder_timeout"):
        buffer.check_timeout(.101)


def test_capacity_and_duplicate_fail_closed():
    buffer = PacketReorderBuffer(capacity=1)
    buffer.offer(2, b"two", 0)
    with pytest.raises(RuntimeError, match="reorder_capacity"):
        buffer.offer(3, b"three", .001)
    with pytest.raises(RuntimeError, match="duplicate_or_stale"):
        buffer.offer(2, b"two", .001)
    assert buffer.offer(1, b"one", .001) == [b"one", b"two"]
    with pytest.raises(RuntimeError, match="duplicate_or_stale"):
        buffer.offer(1, b"restart", .002)


def test_java_reorder(tmp_path):
    java_home = os.environ.get("JAVA_HOME", "")
    javac = shutil.which("javac") or str(Path(java_home) / "bin/javac")
    java = shutil.which("java") or str(Path(java_home) / "bin/java")
    if not Path(javac).is_file() or not Path(java).is_file():
        pytest.skip("JDK required for Publisher reorder test")
    harness = tmp_path / "ReorderCheck.java"
    harness.write_text("""
package com.ydtrader.mvp;
import java.nio.ByteBuffer;
public class ReorderCheck {
    static ByteBuffer p(int value) { return ByteBuffer.allocate(4).putInt(0,value); }
    static void eq(int a,int b) { if(a!=b) throw new AssertionError(a+" != "+b); }
    static void fails(Runnable f,String reason) {
        try { f.run(); } catch(IllegalStateException e) {
            if(!e.getMessage().contains(reason)) throw e;
            return;
        }
        throw new AssertionError("expected "+reason);
    }
    public static void main(String[] args) {
        UdpReorderBuffer b=new UdpReorderBuffer("test",100,1);
        ByteBuffer original=p(2);
        b.offer(2,original,0); original.putInt(0,999);
        if(b.poll(1)!=null) throw new AssertionError();
        fails(()->b.offer(2,p(2),1),"duplicate_or_stale");
        fails(()->b.offer(3,p(3),1),"reorder_capacity");
        b.offer(1,p(1),2);
        eq(b.poll(3).getInt(),1); eq(b.poll(3).getInt(),2);
        fails(()->b.offer(1,p(1),4),"duplicate_or_stale");
        UdpReorderBuffer gap=new UdpReorderBuffer("gap",100,8);
        gap.offer(3,p(3),0); gap.offer(4,p(4),90_000_000);
        gap.offer(1,p(1),95_000_000); eq(gap.poll(95_000_000).getInt(),1);
        fails(()->gap.poll(100_000_000),"reorder_timeout");
        UdpReorderBuffer normal=new UdpReorderBuffer("normal",100,8);
        for(int i=1;i<=10000;i++) {
            normal.offer(i,p(i),i); eq(normal.poll(i).getInt(),i);
        }
        System.out.println("JAVA_REORDER_CHECK_PASS");
    }
}
""", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    source = root / "aeron_mvp/src/com/ydtrader/mvp/UdpReorderBuffer.java"
    subprocess.run([javac, "-d", str(tmp_path), str(source), str(harness)], check=True)
    result = subprocess.run([java, "-cp", str(tmp_path),
        "com.ydtrader.mvp.ReorderCheck"], check=True, capture_output=True, text=True)
    assert "JAVA_REORDER_CHECK_PASS" in result.stdout
