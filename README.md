# WaybackWolf

**WaybackWolf** is a powerful command-line tool designed to analyze URLs by checking their accessibility, retrieving archived snapshots from the Wayback Machine, and detecting sensitive data using AI. It supports interactive mode for live content display and offers flexible configuration for both technical and security-focused users.


## Features

- **URL Accessibility Check**: Validates if URLs are live with customizable timeouts.
- **Wayback Machine Snapshots**: Retrieves the latest archived versions of inaccessible URLs.
- **AI-Powered Analysis**: Detects sensitive data (e.g., PII, credentials) using ChatGPT, Grok (future), or DeepSeek R-1 via Ollama.
- **Interactive Mode**: Displays content and AI analysis in real-time with controls ('p' to pause, 's' to skip, 'q' to quit).
- **Adaptive Concurrency**: Adjusts workers based on system resources for optimal performance.
- **Content Caching**: Speeds up repeated runs by caching fetched content locally.
- **Flexible Output**: Saves results as plain text or JSON with summary statistics.
- **Non-Text Support**: Extracts text from `.pdf` and `.docx` files for AI analysis.

## Installation

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/AIwolfie/waybackwolf.git
   cd waybackwolf

2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Ollama (for DeepSeek R-1)**:
   - Follow [Ollama installation instructions](https://ollama.ai/) and pull the DeepSeek R-1 model:
     ```bash
     ollama pull deepseek-r1
     ```

4. **Create Config File**:
   - Create a `waybackwolf_config.json` in the repo root:
     ```json
     {
       "chatgpt": "your_openai_api_key",
       "grok": "your_xai_grok_api_key"
     }
     ```
   - Replace with your actual API keys. Grok support is pending xAI API availability.

## Usage

Run the tool with `python waybackwolf.py` and the desired options:

### Basic Check
Check URLs and save results:
```bash
python waybackwolf.py -i urls.txt -o results.txt
```

### AI Analysis
Analyze specific extensions with ChatGPT:
```bash
python waybackwolf.py -i urls.txt --ai chatgpt --extensions .json .sql -j results.json
```

### Interactive Mode
View content and analysis live:
```bash
python waybackwolf.py -i urls.txt --ai deepseek --extensions .pdf .docx --interactive
```

### Full Options
```bash
python waybackwolf.py -i urls.txt -d example.com -o results.txt -j results.json -w 20 -ww 5 --ai chatgpt --extensions .sql .json --interactive --connect-timeout 10 --read-timeout 15 --clear-cache
```

### Command-Line Arguments
| Flag                   | Description                                      | Default         |
|-----------------------|--------------------------------------------------|-----------------|
| `-i/--input`          | Input file with URLs (required)                  | -               |
| `-o/--output`         | Output file for plain text results               | -               |
| `-j/--json`           | Output file for JSON results                     | -               |
| `-d/--domain`         | Filter URLs by domain                            | -               |
| `-w/--workers`        | Max URL check workers (adjusted dynamically)     | 10              |
| `-ww/--wayback-workers` | Max Wayback workers (adjusted dynamically)     | 5               |
| `--ai`                | AI for analysis: `chatgpt`, `grok`, `deepseek`   | -               |
| `--extensions`        | Extensions to analyze (e.g., `.sql .json`)       | -               |
| `--interactive`       | Enable interactive mode                          | False           |
| `--connect-timeout`   | Connection timeout (seconds)                     | 5               |
| `--read-timeout`      | Read timeout (seconds)                           | 10              |
| `--clear-cache`       | Clear content cache before running               | False           |

Run `python waybackwolf.py --help` for full details.

## Example Output
```
=== WaybackWolf ===
[ASCII Art and Credits]

Adjusted workers: URL=4, Wayback=2 based on system resources

=== URL Breakdown by Extension ===
----------------------------------
.json      :   1
.sql       :   1
.tar.gz    :   1
----------------------------------

=== Checking URL Status ===
[Interactive Display or Progress Bar]

=== Results ===
Accessible URLs:
✔ https://example.com/data.json - 200 OK (Accessible)
    AI Analysis: Sensitive data detected: API keys found.

Inaccessible URLs:
✗ https://example.com/private.sql - 404 (Latest Snapshot: ...)
    AI Analysis: Sensitive data detected: Credentials found.
⚠ https://example.com/backup.tar.gz - Timeout (Failed after 3 retries)

=== Summary Statistics ===
Total URLs Processed: 3
Accessible URLs: 1
Inaccessible URLs: 2
URLs with Sensitive Data: 2
```

## Contributing

Contributions are welcome! Please:
1. Fork the repo.
2. Create a feature branch (`git checkout -b feature/new-thing`).
3. Commit changes (`git commit -m "Add new thing"`).
4. Push to the branch (`git push origin feature/new-thing`).
5. Open a Pull Request.

## License

Released under the [MIT License](LICENSE).

## Credits

Developed by **AIwolfie**. Special thanks to contributors and the open-source community!

---

### **Instructions**
1. **Save Files**:
   - Save `requirements.txt` as-is in the repo root.
   - Save `README.md` as-is in the repo root.

2. **GitHub Setup**:
   - Create a repo at `https://github.com/AIwolfie/waybackwolf`.
   - Add these files along with `waybackwolf.py`.
   - Include a `LICENSE` file with the MIT License text.

3. **Testing**:
   - Test the `requirements.txt` by running `pip install -r requirements.txt` in a fresh virtual environment.
   - Preview the `README.md` on GitHub to ensure formatting looks good.

Let me know if you’d like to tweak anything (e.g., add a logo, adjust versions, or expand the README)! Ready to push to GitHub?
