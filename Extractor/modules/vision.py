#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import shutil
import logging
import zipfile
from typing import Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from pyrogram.types import Message
from pyrogram import Client
from Extractor.core.utils import forward_to_log

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants
BASE_URL = "https://www.visionias.in"
TMP_DIR = "tmp_downloads"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

class VisionIASExtractor:
    def __init__(self, app: Optional[Client] = None, message: Optional[Message] = None):
        self.session = requests.Session()
        self.app = app
        self.message = message
        self.cookies = {}
        self.video_urls = []
        self.pdf_files = []

        if not os.path.exists(TMP_DIR):
            os.makedirs(TMP_DIR)

    async def send_message(self, text: str):
        if self.app and self.message:
            await self.message.edit_text(text)
        else:
            print(text)

    def get_video_url(self, video_page_url: str) -> Optional[str]:
        try:
            video_page = self.session.get(
                f"{BASE_URL}/student/pt/video_student/{video_page_url}",
                headers=HEADERS,
                cookies=self.cookies,
                verify=False
            ).text
            soup = BeautifulSoup(video_page, 'html.parser')
            iframe = soup.select_one('.js-video iframe')
            if iframe and iframe.get('src'):
                return iframe['src']
        except Exception as e:
            logger.error(f"Error getting video URL: {e}")
        return None

    async def login(self, user_id: str, password: str) -> bool:
        try:
            login_headers = HEADERS.copy()
            login_headers.update({
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/student/module/login.php"
            })

            payload = {
                "login": user_id,
                "password": password,
                "returnUrl": "student"
            }

            login_response = self.session.post(
                f"{BASE_URL}/student/module/login-exec2test.php",
                data=payload,
                headers=login_headers,
                verify=False
            )

            if "Invalid" in login_response.text:
                await self.send_message("❌ Invalid credentials!")
                return False

            self.cookies = dict(login_response.cookies)
            HEADERS["Cookie"] = "; ".join([f"{k}={v}" for k, v in self.cookies.items()])
            
            batch_response = self.session.get(
                f"{BASE_URL}/student/pt/video_student/live_class_dashboard.php",
                headers=HEADERS,
                cookies=self.cookies,
                verify=False
            )
            
            soup = BeautifulSoup(batch_response.text, 'html.parser')
            course_divs = soup.find_all('div', class_='grid-one-third alpha phn-tab-grid-full phn-tab-mb-30')
            
            if not course_divs:
                await self.send_message("❌ No batches found!")
                return False
            
            batch_list = []
            for div in course_divs:
                course_name = div.find('h4').text.strip()
                batch_id = div.find('p', class_='ldg-sectionAvailableCourses_classes')
                if batch_id:
                    batch_id = batch_id.text.strip().replace('(', '').replace(')', '')
                    batch_list.append(f"🔹 `{batch_id}` - {course_name}")
            
            await self.send_message(f"""
✅ Login Successful!
👤 User: {user_id}

📚 Available Batches:

{chr(10).join(batch_list)}

Send batch ID to start extraction...
""")
            return True

        except Exception as e:
            await self.send_message(f"❌ Login error: {str(e)}")
            return False

    async def extract_video_urls(self, batch_id: str) -> bool:
        try:
            await self.send_message("🔄 Initializing Video Extraction...")

            current_headers = HEADERS.copy()
            current_headers.update({
                "Referer": f"https://visionias.in/student/pt/video_student/video_student_dashboard.php?package_id={batch_id}"
            })

            dashboard_response = self.session.get(
                f"{BASE_URL}/student/pt/video_student/video_student_dashboard.php",
                params={'package_id': batch_id},
                headers=current_headers,
                cookies=self.cookies,
                verify=False
            )

            video_ids = list(set(re.findall(r'vid=(\d+)', dashboard_response.text)))
            if not video_ids:
                await self.send_message("❌ No Videos Found in package!")
                return False

            for vid in video_ids:
                response = self.session.get(
                    'https://visionias.in/student/pt/video_student/video_class_timeline_dashboard.php',
                    params={'vid': vid, 'package_id': batch_id},
                    cookies=self.cookies,
                    headers=current_headers,
                    verify=False
                )
                soup = BeautifulSoup(response.text, "html.parser")
                links = soup.select("ul.gw-submenu a")
                for link in links:
                    name = link.get_text(strip=True)
                    url = link.get("href")
                    if url:
                        self.video_urls.append(f"{name}: {url}")

            if self.video_urls:
                file_path = f"{batch_id}_videos.txt"
                with open(file_path, "w", encoding="utf-8") as f:
                    for i, url in enumerate(self.video_urls, 1):
                        f.write(f"{i}. {url}\n")
                return True

            return False

        except Exception as e:
            await self.send_message(f"❌ Video extraction failed: {e}")
            return False

    async def download_pdfs(self, batch_id: str) -> bool:
        try:
            response = self.session.get(
                f'{BASE_URL}/student/pt/video_student/all_handout.php',
                params={'package_id': batch_id},
                headers=HEADERS,
                cookies=self.cookies,
                verify=False
            ).text

            soup = BeautifulSoup(response, 'html.parser')
            li_tags = soup.find_all('li', id='card_type')

            for li in li_tags:
                title = li.find('div', class_='card-body_custom').text.strip()
                url = li.find('a')['href']
                safe_title = "".join(x for x in title if x.isalnum() or x in "._- ")

                pdf_response = self.session.get(
                    f"{BASE_URL}/student/pt/video_student/{url}",
                    headers=HEADERS,
                    cookies=self.cookies,
                    verify=False,
                    stream=True
                )

                if pdf_response.status_code == 200:
                    pdf_path = os.path.join(TMP_DIR, f"{safe_title}.pdf")
                    with open(pdf_path, 'wb') as f:
                        for chunk in pdf_response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    self.pdf_files.append(pdf_path)

            return bool(self.pdf_files)

        except Exception as e:
            await self.send_message(f"❌ PDF download failed: {e}")
            return False

    def create_zip(self, batch_name: str):
        if self.pdf_files:
            zip_path = f"{batch_name}_PDFs.zip"
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for pdf in self.pdf_files:
                    zipf.write(pdf, os.path.basename(pdf))
            return zip_path
        return None

    def cleanup(self):
        for pdf in self.pdf_files:
            try:
                os.remove(pdf)
            except:
                pass

        if os.path.exists(TMP_DIR) and not os.listdir(TMP_DIR):
            os.rmdir(TMP_DIR)

    async def extract_batch(self, batch_id: str, batch_name: str):
        await self.send_message(f"🚀 Starting Extraction: {batch_name}")

        await self.extract_video_urls(batch_id)
        await self.download_pdfs(batch_id)
        zip_path = self.create_zip(batch_name)

        if self.app and self.message:
            video_file = f"{batch_id}_videos.txt"
            if os.path.exists(video_file):
                await self.app.send_document(self.message.chat.id, video_file, caption="📄 Video Links")

            if zip_path and os.path.exists(zip_path):
                await self.app.send_document(self.message.chat.id, zip_path, caption="📚 PDF Archive")

        self.cleanup()

    async def run(self):
        try:
            if self.app and self.message:
                await self.send_message("🔐 Send credentials: <code>email*password</code>")
                response = await self.app.listen(self.message.chat.id, timeout=300)
                await forward_to_log(response, "Vision IAS Extractor")
                creds = response.text.strip()
            else:
                creds = input("Enter ID*Password: ")

            user_id, password = creds.split('*', 1)
            if not await self.login(user_id.strip(), password.strip()):
                return

            if self.app and self.message:
                response = await self.app.listen(self.message.chat.id, timeout=300)
                batch_id = response.text.strip()
            else:
                batch_id = input("Enter batch ID: ")

            await self.extract_batch(batch_id, f"Batch_{batch_id}")

        except Exception as e:
            await self.send_message(f"❌ Error: {e}")
        finally:
            self.cleanup()
            try:
                self.session.get(f'{BASE_URL}/student/logout.php', headers=HEADERS)
            except:
                pass

async def scrape_vision_ias(app: Optional[Client] = None, message: Optional[Message] = None):
    extractor = VisionIASExtractor(app, message)
    await extractor.run()

def main():
    import asyncio
    asyncio.run(scrape_vision_ias())

if __name__ == "__main__":
    main()
