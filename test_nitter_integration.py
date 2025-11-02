"""
Test Nitter integration with Bond pipeline
"""
import asyncio
from engine.schemas import StreamSpec
from apps.runtime import StreamRuntime


async def test_nitter_stream():
    """Test creating a stream with Nitter source"""

    # Create stream spec with Nitter source
    spec = StreamSpec(
        symbols=["BTC"],  # Not really used for Nitter, but required by schema
        interval_sec=5,  # Check every 5 seconds
        sources=[
            {
                "type": "nitter",
                "username": "elonmusk",
                "interval_sec": 5
            }
        ]
    )

    print("="*70)
    print("Testing Nitter Integration with Bond")
    print("="*70)
    print(f"Stream spec: {spec.model_dump()}")
    print("\nStarting runtime...")
    print("This will launch Playwright and start scraping @elonmusk tweets")
    print("Press Ctrl+C to stop\n")

    runtime = StreamRuntime()

    try:
        await runtime.start()
        print("✅ Runtime started\n")

        # Launch stream
        stream_id = "test_nitter_stream"
        await runtime.launch_stream(stream_id, spec)
        print(f"✅ Stream {stream_id} launched\n")

        # Keep running for 60 seconds
        print("Stream is running... (will run for 60 seconds)")
        await asyncio.sleep(60)

    except KeyboardInterrupt:
        print("\n\n⏹️  Stopped by user")
    finally:
        print("\nStopping runtime...")
        await runtime.stop()
        print("✅ Runtime stopped")


if __name__ == "__main__":
    asyncio.run(test_nitter_stream())
