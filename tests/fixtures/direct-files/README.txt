seek.webm is a synthetic 3-second 160x120 test pattern at 10 fps, generated locally with:
ffmpeg -v error -f lavfi -i testsrc=size=160x120:rate=10 -t 3 -c:v libvpx seek.webm
It contains no recorded footage. Used to verify direct delivery and video seeking in Chromium.
