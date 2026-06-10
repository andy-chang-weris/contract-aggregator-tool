# Setting Up a Cron Job for the Contract Aggregator

This guide explains how to configure a Linux cron job (e.g., on an AWS EC2 instance) to run `run_all.py` on a schedule.

## Prerequisites

* Your application is deployed on a Linux server.
* `run_all.py` successfully runs from the command line.
* Python and all project dependencies are installed.
* (Recommended) A Python virtual environment is configured.

---

## Step 1: Connect to Your Server

SSH into your server:

```bash
ssh ubuntu@YOUR_SERVER_IP
```

Replace `YOUR_SERVER_IP` with your server's public IP address.

---

## Step 2: Navigate to the Project Directory

```bash
cd /home/ubuntu/contract-aggregator-tool
```

Verify that `run_all.py` exists:

```bash
ls
```

Expected output should include:

```text
run_all.py
proxy.py
requirements.txt
...
```

---

## Step 3: Determine the Python Executable Path

### Option A: System Python

```bash
which python3
```

Example output:

```text
/usr/bin/python3
```

### Option B: Virtual Environment (Recommended)

Activate the virtual environment:

```bash
source venv/bin/activate
```

Then locate Python:

```bash
which python
```

Example output:

```text
/home/ubuntu/contract-aggregator-tool/venv/bin/python
```

Save this path for the cron configuration.

---

## Step 4: Test the Scraper Manually

Before scheduling, ensure the scraper works correctly.

### Using System Python

```bash
python3 run_all.py
```

### Using a Virtual Environment

```bash
/home/ubuntu/contract-aggregator-tool/venv/bin/python run_all.py
```

Verify that:

* Data is successfully scraped.
* Data is stored in the database.
* No errors occur.

---

## Step 5: Open the Cron Editor

```bash
crontab -e
```

If prompted for an editor, select `nano`.

---

## Step 6: Add the Cron Job

### Every 6 Hours (System Python)

```cron
0 */6 * * * cd /home/ubuntu/contract-aggregator-tool && /usr/bin/python3 run_all.py >> scrape.log 2>&1
```

### Every 6 Hours (Virtual Environment)

```cron
0 */6 * * * cd /home/ubuntu/contract-aggregator-tool && /home/ubuntu/contract-aggregator-tool/venv/bin/python run_all.py >> scrape.log 2>&1
```

### What This Does

```text
Every 6 hours
    ↓
Change into project directory
    ↓
Run run_all.py
    ↓
Append output to scrape.log
    ↓
Capture both stdout and stderr
```

---

## Step 7: Save and Exit

If using Nano:

```text
CTRL + O
Enter
CTRL + X
```

---

## Step 8: Verify the Cron Job

List all configured cron jobs:

```bash
crontab -l
```

You should see your newly added schedule.

---

## Step 9: Monitor Logs

View the log file:

```bash
cat scrape.log
```

Watch logs in real time:

```bash
tail -f scrape.log
```

---

## Common Cron Schedules

### Every 30 Minutes

```cron
*/30 * * * *
```

### Every Hour

```cron
0 * * * *
```

### Every 6 Hours

```cron
0 */6 * * *
```

### Once Per Day at Midnight

```cron
0 0 * * *
```

### Every Day at 8:00 AM

```cron
0 8 * * *
```

---

## Recommended Configuration for This Project

If using a virtual environment:

```cron
0 */6 * * * cd /home/ubuntu/contract-aggregator-tool && /home/ubuntu/contract-aggregator-tool/venv/bin/python run_all.py >> scrape.log 2>&1
```

This will run the complete scraping pipeline every 6 hours and append all output to `scrape.log`.
