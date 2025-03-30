import logging, traceback, re, subprocess, os, tempfile
import requests # 3rd party librart
from secrets import token_hex
from shutil import which
from time import sleep
from typing import Optional
from pathlib import Path
from urllib.parse import parse_qs, urlparse

class EmptyTasksException(Exception):
    pass

class InvalidLinkException(Exception):
    pass

class RateLimitException(Exception):
    pass

class PasswordRequiredException(Exception):
    pass

class InvalidPasswordException(Exception):
    pass

class CannotContinueException(Exception):
    pass

htmlFormPattern = re.compile(r'<input type="hidden" name="([^"]*)" id="\1" value="([^"]*)" \/>')
hostURLPattern = re.compile(r'https:\/\/.*\.sharepoint\.com')
passwordPostURLPattern = re.compile(r'action="([^"]*)"')
webAbsoluteUrlPattern = re.compile(r'"webAbsoluteUrl":"([^"]+)"')

class Downloader:
    def __init__(self,
                 user_agent: str,
                 retry_wait_time: int,
                 tasks: list,
                 simultaneous_transfers: int,
                 file_limit: Optional[int] = None,
                 initial_file: Optional[str] = None):
        self.skippedTasks = 0
        self.completedTasks = 0
        self.user_agent = user_agent
        self.retry_wait_time = retry_wait_time
        self.file_limit = file_limit
        self.initial_file = initial_file
        self.headers = {
            "User-Agent": user_agent
        }      
        if len(tasks) == 0:
            raise EmptyTasksException("Task list is empty")
        for task in tasks:
            if "sharepoint.com" not in task["link"]:
                raise InvalidLinkException(f"'{task['link']}' is not a sharepoint.com link.")
            elif "onedrive.aspx" in task["link"]:
                raise InvalidLinkException(f"'{task['link']}' contains 'onedrive.aspx', this link is not usable for this program, you need to put in a link that redirects you to the link.")
            Path(task['downloadTo']).mkdir(parents=True, exist_ok=True)
        self.tasks = tasks
        self.simultaneous_transfers = simultaneous_transfers
    
    def run(self):
        taskIndex = 0
        numberOfTasks = len(self.tasks)
        while taskIndex < numberOfTasks:
            try:
                logging.info(f"Running task {taskIndex + 1}/{numberOfTasks}")
                task = self.tasks[taskIndex]
                session = requests.session()
                response = session.get(task["link"], headers=self.headers, timeout=20)
                if response.status_code == 429:
                    raise RateLimitException()
                elif response.status_code > 299:
                    raise CannotContinueException(f"While reading file list, program encountered unknown HTTP response code {response.status_code}")
                if "You've received a link to a folder that requires a password" in response.text or 'input id="txtPassword"' in response.text or 'input name="txtPassword"' in response.text:
                    if password := task["password"]:
                        # Extract post url
                        if (host := hostURLPattern.match(task["link"])) and (formAction := passwordPostURLPattern.search(response.text)):
                            passwordSubmitUrl = host.group(0) + formAction.group(1).replace("amp;","")
                        else:
                            raise CannotContinueException("Link has required password, but program failed to find password submit url")
                        
                        # Extract html form
                        payload = {}
                        for match in htmlFormPattern.finditer(response.text):
                            payload[match.group(1)] = match.group(2)
                        payload["__EVENTTARGET"] = "btnSubmitPassword"
                        payload["__EVENTARGUMENT"] = ""
                        payload["txtPassword"] = task["password"]
                        logging.debug(f"Password submit url: {passwordSubmitUrl}")
                        response = session.post(passwordSubmitUrl, headers=self.headers, data=payload, timeout=20)
                        if response.status_code == 429:
                            raise RateLimitException()
                        elif response.status_code > 299:
                            raise CannotContinueException(f"While reading file list, program encountered unknown HTTP response code {response.status_code}")
                        if "You've received a link to a folder that requires a password" in response.text or 'input id="txtPassword"' in response.text:
                            raise InvalidPasswordException()
                    else:
                        raise PasswordRequiredException("Password is required but a password is not given, skipping task")
                
                cookies = response.cookies
                if "FedAuth" not in cookies:
                    raise CannotContinueException("Downloader failed to get cookie from Sharepoint")
                
                params = parse_qs(urlparse(response.url).query)
                if "id" not in params:
                    raise CannotContinueException("Can't determine webdav endpoint")
                
                if match := webAbsoluteUrlPattern.search(response.text):
                    webdavEndpoint = match.group(1) + "/" + "/".join(params["id"][0].split("/")[3:])
                    logging.debug(f"webdav Endpoint: {webdavEndpoint}")
                
                with open("sharepoint_rclone.conf", mode="w", encoding="utf8") as f:
                    f.write("[webdav]\n")
                    f.write("type = webdav\n")
                    f.write(f"url = {webdavEndpoint}\n")
                    f.write("vendor = other\n")
                    f.write(f"headers = Cookie,FedAuth={cookies['FedAuth']}")
                
                # Create temp directory for list files
                temp_dir = tempfile.mkdtemp()
                files_list_path = os.path.join(temp_dir, "files_list.txt")
                batch_list_path = os.path.join(temp_dir, "batch_files.txt")
                
                # List all available files
                logging.info("Listing all available files...")
                list_cmd = [
                    which("rclone"), 
                    "--config", "sharepoint_rclone.conf",
                    "lsf", 
                    "--files-only",
                    "webdav:"
                ]
                
                with open(files_list_path, "w", encoding="utf-8") as f:
                    subprocess.run(list_cmd, stdout=f)
                
                # Check if there are files to download
                if os.path.getsize(files_list_path) == 0:
                    logging.info("No files found for download")
                    taskIndex += 1
                    continue
                
                # Count total files
                with open(files_list_path, "r", encoding="utf-8") as f:
                    total_files = sum(1 for _ in f)
                
                logging.info(f"Total files found: {total_files}")
                
                # Read file list
                with open(files_list_path, "r", encoding="utf-8") as f:
                    file_lines = f.readlines()
                
                # Find starting point based on initial file
                start_index = 0
                if self.initial_file:
                    logging.info(f"Looking for initial file: {self.initial_file}")
                    for i, line in enumerate(file_lines):
                        if self.initial_file in line:
                            start_index = i
                            logging.info(f"Starting from file at index {start_index}: {line.strip()}")
                            break
                    if start_index == 0 and self.initial_file:
                        logging.warning(f"Initial file '{self.initial_file}' not found, starting from the beginning")
                
                # Use slice from start_index
                file_lines = file_lines[start_index:]
                
                # Determine how many files to download after finding starting point
                remaining_files = len(file_lines)
                files_to_download = min(remaining_files, self.file_limit) if self.file_limit else remaining_files
                
                logging.info(f"Planning to download {files_to_download} files starting from index {start_index}")
                
                # If there's a file limit, truncate the list
                if self.file_limit:
                    file_lines = file_lines[:self.file_limit]
                
                # Start download in batches
                downloaded_count = 0
                
                # Process in batches
                batch_size = 100  # Batch size, can be adjusted as needed
                
                for i in range(0, len(file_lines), batch_size):
                    if downloaded_count >= files_to_download:
                        break
                    
                    # Create batch file
                    batch = file_lines[i:min(i+batch_size, len(file_lines))]
                    with open(batch_list_path, "w", encoding="utf-8") as f:
                        f.writelines(batch)
                    
                    # Download the batch
                    current_batch_size = len(batch)
                    logging.info(f"Downloading batch of {current_batch_size} files ({downloaded_count+1}-{downloaded_count+current_batch_size} of {files_to_download})")
                    
                    copy_cmd = [
                        which("rclone"),
                        "--config", "sharepoint_rclone.conf",
                        "copy",
                        "--progress",
                        "--transfers", str(self.simultaneous_transfers),
                        "--files-from-raw", batch_list_path,
                        "webdav:",
                        task['downloadTo']
                    ]
                    
                    rclone = subprocess.run(copy_cmd)
                    
                    if rclone.returncode != 0:
                        logging.info(f"rclone exited with status code {rclone.returncode} which indicates an error, sleep for {self.retry_wait_time} seconds")
                        sleep(self.retry_wait_time)
                        # Continue with next batch even if there's an error
                    
                    downloaded_count += current_batch_size
                    logging.info(f"Progress: {downloaded_count}/{files_to_download} files downloaded")
                
                # Clean up temporary files
                try:
                    os.remove(files_list_path)
                    os.remove(batch_list_path)
                    os.rmdir(temp_dir)
                except:
                    logging.debug("Could not remove temporary files")
                
                logging.info(f"Download completed for task {taskIndex + 1}")
                self.completedTasks += 1
                taskIndex += 1
                
            except PasswordRequiredException:
                logging.info("Password is required but no password is given, skip to next task.")
                taskIndex += 1
                self.skippedTasks += 1
            except InvalidPasswordException:
                logging.info(f"Password is required and the password given is invalid, skip to next task.")
                taskIndex += 1
                self.skippedTasks += 1
            except RateLimitException:
                logging.info(f"Link given is currently rate limited, sleep for {self.retry_wait_time} seconds.")
                sleep(self.retry_wait_time)
            except CannotContinueException as e:
                logging.info(f"Downloader encountered a problem that can't be solved: {e}")
                logging.info("Skip to next task.")
                taskIndex += 1
                self.skippedTasks += 1
            except Exception as e:
                logging.info(f"Enountered an unknown error {e} while running task, sleep for {self.retry_wait_time} seconds.")
                logging.debug(traceback.format_exc())
                sleep(self.retry_wait_time)
