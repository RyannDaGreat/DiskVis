<img width="1728" height="947" alt="image" src="https://github.com/user-attachments/assets/8174c2c7-7ad7-4ae8-b424-9abd3573df94" />
![Screen Recording 2026-02-06 at 5 52 06 AM](https://github.com/user-attachments/assets/a6513803-b77a-48b8-8e51-539132312f43)

Inspect and analyze disk usage with a beautiful web UI featuring treemaps (i.e. GrandPerspective) and sunburst (i.e. DaisyDisk) for any filesystem, even remote ones, directly in your browser.
It features a real time, zoomable and pannable interface for maximum interactivity - great for Mac trackpads.

To use:

TLDR: You first run python programs to get a 'census' of the files on your system - then you drag that file into the web browser at ryanndagreat.github.io/DiskVis to see what's inside it.

First run `python scan_census.py scan / full_census.zst` to get a full list of all files on your disk, written to `full_census.zst`. You can change the `/` to some other directory if you wish to scan a subset.
If it takes a long time to scan, don't worry - it saves checkpoints periodically so if it crashes you can run it again and it will resume where it left off.

That file is basically compressed JSON - but since it lists every file on your drive it might be quite large. Dropping over 50mb into the browser might make it slow, so to compress it, use `python lod_census.py full_census.zst -t 2000000` to get a new file like `full_census.zst.lod.census.zst` which will be much smaller. To control the size of this, adjust the `2000000` to a larger or smaller number. Basically it prunes small files from the visual graphs to make it less laggy.
