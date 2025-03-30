# pySharepointDownloader

A script that utilizes rclone to download sharepoint links you received. Ever got a sharepoint folder link that has hundreds of files that each is 1GB each, and the zip download button is useless? This script is for you.

## New Features in This Fork

This fork adds the following improvements:
- Choose which file to start downloading from
- Limit the number of files to download
- Perfect for large folders where you only need a subset of files

## Requirements
1. [Python3](https://www.python.org/downloads/) (3.8 and later should be OK, but 3.11 was used when developing this script)
2. [rclone](https://rclone.org/) (Must be available at PATH, or on Windows put it in pySharepointDownloader folder)

## Usage
1. [Download](https://github.com/M4lho/pySharepointDownloader/archive/refs/heads/main.zip) and extract pySharepointDownloader
2. Use a text editor (like Notepad) to edit config.py
3. If you are a Windows user, click "run.bat"
4. If you are a Linux user, use run.sh (Might work for other Unix file systems but not tested)

## Configuration Options

In config.py, you can now set:

```python
# Limit of files to download, set to None to download all files
# Example: file_limit = 200 to download only 200 files
file_limit = None

# Initial file to start downloading from (set to None to start from the beginning)
# Example: initial_file = "Example" to start downloading from a file named "Example"
initial_file = None