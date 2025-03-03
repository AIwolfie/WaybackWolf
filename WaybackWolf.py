#!/usr/bin/env python3

import argparse
import asyncio
import json
import re
import os
import aiohttp
import aiofiles
from collections import defaultdict
from urllib.parse import urlparse
from tqdm import tqdm
from colorama import init, Fore, Style
import waybackpy
from concurrent.futures import ThreadPoolExecutor
import time
import logging
import openai
import ollama
from xai import Grok  # Hypothetical xAI Grok API client (replace with actual library if available)
from rich.console import Console
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
import psutil
import hashlib
import PyPDF2
import docx

# Initialize colorama, logging, and rich console
init()
logging.basicConfig(filename='waybackwolf.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
console = Console()

TOOL_NAME = "WaybackWolf"
CREDIT_TEXT = f"""
[bold cyan]

 __    __            _                _     __    __      _  __ 
/ / /\ \ \__ _ _   _| |__   __ _  ___| | __/ / /\ \ \___ | |/ _|
\ \/  \/ / _` | | | | '_ \ / _` |/ __| |/ /\ \/  \/ / _ \| | |_ 
 \  /\  / (_| | |_| | |_) | (_| | (__|   <  \  /\  / (_) | |  _|
  \/  \/ \__,_|\__, |_.__/ \__,_|\___|_|\_\  \/  \/ \___/|_|_|  
               |___/                                            

[/]
[green]A fast and efficient tool for analyzing archived URLs, retrieving snapshots, and detecting sensitive data.[/]

[green]Developed by: [white]AIwolfie[/]
[green]Version: [white]1.0.0[/]
[green]GitHub: [white]https://github.com/AIwolfie/waybackwolf[/]
[green]License: [white]MIT[/]

[yellow]----------------------------------[/]
"""

EXTENSIONS = [
    '.xls', '.xml', '.xlsx', '.json', '.pdf', '.sql', '.doc', '.docx', '.pptx', '.txt',
    '.zip', '.tar.gz', '.tgz', '.bak', '.7z', '.rar', '.log', '.cache', '.secret', '.db',
    '.backup', '.yml', '.gz', '.config', '.csv', '.yaml', '.md', '.md5', '.exe', '.dll',
    '.bin', '.ini', '.bat', '.sh', '.tar', '.deb', '.rpm', '.iso', '.img', '.apk', '.msi',
    '.dmg', '.tmp', '.crt', '.pem', '.key', '.pub', '.asc'
]
EXTENSIONS_SET = set(EXTENSIONS)

# Cache and thread pool
WAYBACK_CACHE = {}
CONTENT_CACHE_DIR = "waybackwolf_cache"
os.makedirs(CONTENT_CACHE_DIR, exist_ok=True)
WAYBACK_EXECUTOR = ThreadPoolExecutor()

# Load API keys from config file
try:
    with open('waybackwolf_config.json', 'r') as f:
        CONFIG = json.load(f)
    openai.api_key = CONFIG.get("chatgpt")
except FileNotFoundError:
    logging.error("waybackwolf_config.json not found. Please create it with API keys.")
    raise
except KeyError:
    logging.error("Invalid config file format. Expected keys: 'chatgpt', 'grok'.")
    raise

def get_extension(url):
    path = urlparse(url).path
    if '.' not in path:
        return None
    ext = '.' + path.split('.')[-1]
    return ext if ext in EXTENSIONS_SET else None

def matches_domain(url, domain):
    if not domain:
        return True
    return urlparse(url).netloc.endswith(domain)

async def read_urls(file_path):
    try:
        async with aiofiles.open(file_path, 'r') as f:
            async for line in f:
                url = line.strip()
                if url:
                    yield url
    except Exception as e:
        logging.error(f"Failed to read input file {file_path}: {e}")
        raise

def count_extensions(urls, domain=None):
    ext_counts = defaultdict(int)
    for url in urls:
        if matches_domain(url, domain):
            ext = get_extension(url)
            if ext:
                ext_counts[ext] += 1
    return ext_counts

async def fetch_content(url, session, semaphore, connect_timeout=5, read_timeout=10):
    cache_key = hashlib.sha256(url.encode()).hexdigest()
    cache_path = os.path.join(CONTENT_CACHE_DIR, f"{cache_key}.txt")
    
    if os.path.exists(cache_path):
        async with aiofiles.open(cache_path, 'r') as f:
            return await f.read()

    async with semaphore:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=None, connect=connect_timeout, sock_read=read_timeout)) as response:
                if response.status == 200:
                    content = await response.text()
                    async with aiofiles.open(cache_path, 'w') as f:
                        await f.write(content)
                    return content
        except Exception as e:
            logging.error(f"Failed to fetch content from {url}: {e}")
    return None

async def extract_text_from_file(content, ext):
    if ext == '.pdf':
        try:
            pdf_reader = PyPDF2.PdfReader(content)
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() or ""
            return text
        except Exception as e:
            logging.error(f"Failed to extract text from PDF: {e}")
            return None
    elif ext == '.docx':
        try:
            doc = docx.Document(content)
            text = "\n".join([para.text for para in doc.paragraphs])
            return text
        except Exception as e:
            logging.error(f"Failed to extract text from DOCX: {e}")
            return None
    return content

async def analyze_content(contents, ai_choice, semaphore):
    prompt = "Analyze the following contents for sensitive or confidential data (e.g., PII, credentials, financial info). Provide a brief summary for each, separated by '---'. Contents:\n\n"
    for i, content in enumerate(contents):
        prompt += f"Content {i+1}:\n{content[:4000]}\n\n"
    
    async with semaphore:
        try:
            if ai_choice == "chatgpt":
                response = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=500
                )
                return response.choices[0].message["content"].split("---")
            elif ai_choice == "grok":
                grok_client = Grok(api_key=CONFIG.get("grok"))
                response = grok_client.chat(prompt)  # Hypothetical Grok API call
                return response.split("---")
            elif ai_choice == "deepseek":
                response = ollama.chat(model="deepseek-r1", messages=[{"role": "user", "content": prompt}])
                return response["message"]["content"].split("---")
        except Exception as e:
            logging.error(f"AI analysis failed: {e}")
            if ai_choice == "chatgpt" and CONFIG.get("grok"):
                logging.info("Falling back to Grok")
                return await analyze_content(contents, "grok", semaphore)
            elif ai_choice == "grok" and "deepseek" in ollama.list()["models"]:
                logging.info("Falling back to DeepSeek")
                return await analyze_content(contents, "deepseek", semaphore)
            return ["Analysis failed due to error."] * len(contents)
    return ["No sensitive data detected."] * len(contents)

async def check_url(url, session, semaphore, connect_timeout=5, read_timeout=10, retries=3, delay=2):
    async with semaphore:
        for attempt in range(retries):
            try:
                async with session.head(url, timeout=aiohttp.ClientTimeout(total=None, connect=connect_timeout, sock_read=read_timeout), allow_redirects=True, max_redirects=10) as response:
                    return response.status, None
            except aiohttp.ClientConnectionError:
                error = "ConnectionError"
            except aiohttp.ClientResponseError as e:
                error = str(e)
            except asyncio.TimeoutError:
                error = "Timeout"
            except Exception as e:
                error = str(e)
                logging.error(f"URL check failed for {url}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(delay)
                continue
        return None, f"{error} (Failed after {retries} retries)"

async def get_latest_snapshot(url, semaphore, max_retries=3, backoff=2):
    if url in WAYBACK_CACHE:
        return WAYBACK_CACHE[url]
    
    async with semaphore:
        loop = asyncio.get_event_loop()
        for attempt in range(max_retries):
            try:
                wayback = await loop.run_in_executor(WAYBACK_EXECUTOR, waybackpy.Url, url)
                latest = await loop.run_in_executor(WAYBACK_EXECUTOR, lambda w: w.newest(), wayback)
                snapshot_url = latest.url if latest else None
                WAYBACK_CACHE[url] = snapshot_url
                return snapshot_url
            except Exception as e:
                logging.error(f"Wayback snapshot failed for {url}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff * (attempt + 1))
                    continue
                WAYBACK_CACHE[url] = None
                return None

async def process_url(url, session, url_semaphore, wayback_semaphore, ai_choice=None, ai_extensions=None, ai_semaphore=None, live=None, stats=None):
    ext = get_extension(url)
    if not ext:
        return None, None

    layout = Layout()
    layout.split_column(
        Layout(name="url", size=1),
        Layout(name="content", size=10),
        Layout(name="analysis", size=5)
    )
    layout["url"].update(Panel(f"Processing: {url}", style="cyan"))

    status, error = await check_url(url, session, url_semaphore)
    cli_result = ""
    json_result = {"url": url, "status": status, "error": error, "snapshot": None, "ai_analysis": None}
    content = None

    if status == 200:
        cli_result = f"{Fore.GREEN}✔ {url} - 200 OK (Accessible){Style.RESET_ALL}"
        stats["accessible"] += 1
        if ai_choice and ext in ai_extensions:
            content = await fetch_content(url, session, url_semaphore)
    else:
        snapshot_url = await get_latest_snapshot(url, wayback_semaphore)
        error_msg = f"{status if status else error}"
        if snapshot_url:
            cli_result = f"{Fore.RED}✗ {url} - {error_msg} (Latest Snapshot: {Fore.CYAN}{snapshot_url}{Style.RESET_ALL})"
            json_result["snapshot"] = snapshot_url
            if ai_choice and ext in ai_extensions:
                content = await fetch_content(snapshot_url, session, url_semaphore)
        else:
            cli_result = f"{Fore.YELLOW}⚠ {url} - {error_msg} (No Snapshot Available){Style.RESET_ALL}"
        stats["inaccessible"] += 1

    if content:
        layout["content"].update(Panel(content[:1000], title="Content Preview", style="yellow"))
        if ext in ['.pdf', '.docx']:
            content = await extract_text_from_file(content, ext)
        if content:
            analysis = (await analyze_content([content], ai_choice, ai_semaphore))[0]
            layout["analysis"].update(Panel(analysis, title="AI Analysis", style="blue"))
            cli_result += f"\n    {Fore.BLUE}AI Analysis: {analysis}{Style.RESET_ALL}"
            json_result["ai_analysis"] = analysis
            if "sensitive" in analysis.lower():
                stats["sensitive"] += 1
    else:
        layout["content"].update(Panel("No content available", style="yellow"))
        layout["analysis"].update(Panel("N/A", style="blue"))

    if live:
        with live:
            live.update(layout)
            await asyncio.sleep(2)

    stats["total"] += 1
    return cli_result, json_result

async def process_urls(input_file, domain=None, output_file=None, json_file=None, max_workers=10, wayback_workers=5, ai_choice=None, ai_extensions=None, interactive=False, connect_timeout=5, read_timeout=10):
    console.print(CREDIT_TEXT)

    urls = set()
    async for url in read_urls(input_file):
        urls.add(url)
    
    if domain:
        urls = {url for url in urls if matches_domain(url, domain)}

    if not urls:
        console.print(f"{Fore.RED}✗ No URLs found matching criteria!{Style.RESET_ALL}")
        return

    # Adaptive concurrency
    cpu_count = psutil.cpu_count()
    mem_percent = psutil.virtual_memory().percent
    max_workers = min(max_workers, max(1, cpu_count // 2 if mem_percent < 80 else cpu_count // 4))
    wayback_workers = min(wayback_workers, max(1, cpu_count // 4 if mem_percent < 80 else cpu_count // 8))
    console.print(f"[yellow]Adjusted workers: URL={max_workers}, Wayback={wayback_workers} based on system resources[/]")

    ext_counts = count_extensions(urls, domain)
    console.print(f"\n{Fore.CYAN}=== URL Breakdown by Extension ==={Style.RESET_ALL}")
    console.print(f"{Fore.YELLOW}----------------------------------{Style.RESET_ALL}")
    for ext, count in sorted(ext_counts.items()):
        console.print(f"{Fore.GREEN}{ext:<10}{Style.RESET_ALL} : {Fore.MAGENTA}{count:>3}{Style.RESET_ALL}")
    console.print(f"{Fore.YELLOW}----------------------------------{Style.RESET_ALL}")

    stats = {"total": 0, "accessible": 0, "inaccessible": 0, "sensitive": 0}
    results = {"accessible": [], "inaccessible": []}
    url_semaphore = asyncio.Semaphore(max_workers)
    wayback_semaphore = asyncio.Semaphore(wayback_workers)
    ai_semaphore = asyncio.Semaphore(1)  # Limit AI requests to avoid rate limits
    console.print(f"\n{Fore.CYAN}=== Checking URL Status ==={Style.RESET_ALL}")

    async with aiohttp.ClientSession() as session:
        if interactive and (ai_choice or ai_extensions):
            session_prompt = PromptSession(multiline=False)
            bindings = KeyBindings()

            @bindings.add('p')
            async def _(event):
                console.print("[yellow]Paused. Press any key to resume...[/]")
                await session_prompt.prompt_async()

            @bindings.add('s')
            async def _(event):
                event.app.exit(result="skip")

            @bindings.add('q')
            async def _(event):
                event.app.exit(result="quit")

            live = Live(console=console, refresh_per_second=4)
            live.start()
            for url in urls:
                action = await session_prompt.prompt_async("", key_bindings=bindings)
                if action == "quit":
                    break
                elif action == "skip":
                    continue
                cli_result, json_result = await process_url(url, session, url_semaphore, wayback_semaphore, ai_choice, ai_extensions, ai_semaphore, live, stats)
                if cli_result:
                    if "✔" in cli_result:
                        results["accessible"].append((cli_result, json_result))
                    else:
                        results["inaccessible"].append((cli_result, json_result))
            live.stop()
        else:
            tasks = [process_url(url, session, url_semaphore, wayback_semaphore, ai_choice, ai_extensions, ai_semaphore, None, stats) for url in urls]
            contents_to_analyze = []
            urls_to_analyze = []
            for future in tqdm(asyncio.as_completed(tasks), total=len(urls), desc=f"{Fore.BLUE}Processing{Style.RESET_ALL}", bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}"):
                cli_result, json_result = await future
                if cli_result:
                    if "✔" in cli_result:
                        results["accessible"].append((cli_result, json_result))
                    else:
                        results["inaccessible"].append((cli_result, json_result))
                    if json_result["ai_analysis"] is None and ai_choice and get_extension(json_result["url"]) in ai_extensions:
                        content = await fetch_content(json_result["url"] if json_result["status"] == 200 else json_result["snapshot"], session, url_semaphore)
                        if content:
                            contents_to_analyze.append(content)
                            urls_to_analyze.append(json_result["url"])
            if contents_to_analyze:
                analyses = await analyze_content(contents_to_analyze, ai_choice, ai_semaphore)
                for url, analysis in zip(urls_to_analyze, analyses):
                    for res_list in (results["accessible"], results["inaccessible"]):
                        for i, (cli, json_res) in enumerate(res_list):
                            if json_res["url"] == url:
                                res_list[i] = (f"{cli}\n    {Fore.BLUE}AI Analysis: {analysis}{Style.RESET_ALL}", {**json_res, "ai_analysis": analysis})
                                if "sensitive" in analysis.lower():
                                    stats["sensitive"] += 1

    console.print(f"\n{Fore.CYAN}=== Results ==={Style.RESET_ALL}")
    console.print(f"{Fore.GREEN}Accessible URLs:{Style.RESET_ALL}")
    for cli_result, _ in sorted(results["accessible"]):
        console.print(cli_result)
    console.print(f"\n{Fore.RED}Inaccessible URLs:{Style.RESET_ALL}")
    for cli_result, _ in sorted(results["inaccessible"]):
        console.print(cli_result)

    console.print(f"\n{Fore.CYAN}=== Summary Statistics ==={Style.RESET_ALL}")
    console.print(f"[green]Total URLs Processed: {stats['total']}[/]")
    console.print(f"[green]Accessible URLs: {stats['accessible']}[/]")
    console.print(f"[red]Inaccessible URLs: {stats['inaccessible']}[/]")
    console.print(f"[yellow]URLs with Sensitive Data: {stats['sensitive']}[/]")

    if output_file:
        try:
            async with aiofiles.open(output_file, 'w') as f:
                plain_results = [re.sub(r'\x1b\[[0-9;]*m', '', cli_result) for cli_result, _ in results["accessible"] + results["inaccessible"]]
                await f.write("\n".join(plain_results))
            console.print(f"\n{Fore.GREEN}✓ Results saved to {output_file}{Style.RESET_ALL}")
        except Exception as e:
            logging.error(f"Failed to write to output file {output_file}: {e}")

    if json_file:
        try:
            async with aiofiles.open(json_file, 'w') as f:
                json_data = {
                    "accessible": [jr for _, jr in results["accessible"]],
                    "inaccessible": [jr for _, jr in results["inaccessible"]],
                    "stats": stats
                }
                await f.write(json.dumps(json_data, indent=2))
            console.print(f"{Fore.GREEN}✓ JSON results saved to {json_file}{Style.RESET_ALL}")
        except Exception as e:
            logging.error(f"Failed to write to JSON file {json_file}: {e}")

def main():
    parser = argparse.ArgumentParser(
        description=f"A colorful CLI tool ({TOOL_NAME}) to analyze URLs by file extension, check their accessibility, retrieve Wayback Machine snapshots, and optionally analyze content with AI interactively.",
        epilog=f"Example: python waybackwolf.py -i out.txt -d example.com -o results.txt -j results.json -w 20 -ww 5 --ai chatgpt --extensions .sql .json --interactive --connect-timeout 10 --read-timeout 15"
    )
    parser.add_argument('-i', '--input', required=True, help="Path to the input file with URLs (one per line), e.g., 'out.txt'.")
    parser.add_argument('-o', '--output', help="Path to save plain text results (optional), e.g., 'results.txt'.")
    parser.add_argument('-j', '--json', help="Path to save JSON results (optional), e.g., 'results.json'.")
    parser.add_argument('-d', '--domain', help="Filter URLs by domain (including subdomains), e.g., 'example.com'.")
    parser.add_argument('-w', '--workers', type=int, default=10, help="Max concurrent URL checks (default: 10, adjusted dynamically).")
    parser.add_argument('-ww', '--wayback-workers', type=int, default=5, help="Max concurrent Wayback requests (default: 5, adjusted dynamically).")
    parser.add_argument('--ai', choices=['chatgpt', 'grok', 'deepseek'], help="AI to use for content analysis: 'chatgpt', 'grok', or 'deepseek'.")
    parser.add_argument('--extensions', nargs='+', help="List of extensions to analyze with AI, e.g., '.sql .json'.")
    parser.add_argument('--interactive', action='store_true', help="Enable interactive mode with content display and controls.")
    parser.add_argument('--connect-timeout', type=int, default=5, help="Connection timeout in seconds (default: 5).")
    parser.add_argument('--read-timeout', type=int, default=10, help="Read timeout in seconds (default: 10).")
    args = parser.parse_args()

    ai_extensions = set(args.extensions) if args.extensions else set()
    asyncio.run(process_urls(args.input, args.domain, args.output, args.json, args.workers, args.wayback_workers, args.ai, ai_extensions, args.interactive, args.connect_timeout, args.read_timeout))

if __name__ == "__main__":
    main()