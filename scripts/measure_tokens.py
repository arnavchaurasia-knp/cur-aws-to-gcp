#!/usr/bin/env python3
import os
import sys
import json
import glob
import re
import csv
from collections import defaultdict
import datetime

def load_data_dir():
    # Load DATA_DIR from .env
    data_dir = "/tmp/cur-web-data" # fallback
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if line.strip().startswith("DATA_DIR="):
                    data_dir = line.strip().split("=", 1)[1].strip('"').strip("'")
                    break
    return data_dir

def get_recent_job(jobs_dir, job_id=None):
    if job_id:
        p = os.path.join(jobs_dir, job_id)
        if os.path.isdir(p):
            return job_id, p
        else:
            print(f"Error: Job directory {p} not found.")
            sys.exit(1)
            
    # Find most recently modified job dir
    job_dirs = glob.glob(os.path.join(jobs_dir, "*"))
    if not job_dirs:
        print(f"No jobs found in {jobs_dir}")
        sys.exit(1)
    
    # Filter to actual directories
    job_dirs = [d for d in job_dirs if os.path.isdir(d)]
    if not job_dirs:
        print(f"No jobs found in {jobs_dir}")
        sys.exit(1)
        
    job_dirs.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    latest_job_dir = job_dirs[0]
    return os.path.basename(latest_job_dir), latest_job_dir

def scan_conv_ids_from_log(job_dir):
    conv_ids = set()
    internal_log = os.path.join(job_dir, "agy-internal.log")
    if os.path.exists(internal_log):
        with open(internal_log, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            # Match UUIDs
            matches = re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', content, re.IGNORECASE)
            for m in matches:
                conv_ids.add(m.lower())
    return conv_ids

def find_conv_ids_by_time(brain_dir, start_time, end_time):
    conv_ids = set()
    transcripts = glob.glob(os.path.join(brain_dir, "*/.system_generated/logs/transcript_full.jsonl"))
    for path in transcripts:
        mtime = os.path.getmtime(path)
        if start_time <= mtime <= end_time:
            conv_id = path.split("/")[-4]
            conv_ids.add(conv_id.lower())
    return conv_ids

def get_step_details(step):
    step_type = step.get("type", "UNKNOWN")
    
    # Check if there are tool calls
    tool_calls = step.get("tool_calls", []) or []
    if tool_calls:
        tc = tool_calls[0]
        name = tc.get("name", "")
        args = tc.get("args", {}) or {}
        if name == "view_file":
            return f"view_file: {args.get('AbsolutePath', '')}"
        elif name == "run_command":
            return f"run_command: {args.get('CommandLine', '')}"
        elif name == "grep_search":
            return f"grep_search: Query='{args.get('Query', '')}' Path='{args.get('SearchPath', '')}'"
        elif name == "list_dir":
            return f"list_dir: {args.get('DirectoryPath', '')}"
        return f"tool_call: {name}"
        
    content = step.get("content", "") or ""
    thinking = step.get("thinking", "") or ""
    
    if step_type == "USER_INPUT":
        # clean and truncate request
        req = content.replace("<USER_REQUEST>", "").replace("</USER_REQUEST>", "").strip()
        req_line = req.split("\n")[0] if req else ""
        return f"user_request: {req_line[:120]}"
    elif step_type == "PLANNER_RESPONSE" and thinking:
        think_line = thinking.replace("\n", " ").strip()
        return f"model_thinking: {think_line[:120]}..."
    
    # Fallback to truncated content
    clean_content = content.replace("\n", " ").strip()
    return clean_content[:120]

def analyze_conversation(brain_dir, conv_id):
    path = os.path.join(brain_dir, conv_id, ".system_generated/logs/transcript_full.jsonl")
    if not os.path.exists(path):
        return None
        
    conv_chars = 0
    conv_steps = 0
    conv_types = defaultdict(int)
    first_prompt = "Unknown"
    steps_list = []
    
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                step = json.loads(line)
            except Exception:
                continue
                
            conv_steps += 1
            
            # Extract content and thinking
            content = step.get("content", "") or ""
            thinking = step.get("thinking", "") or ""
            chars = len(content) + len(thinking)
            
            tool_calls = step.get("tool_calls", []) or []
            if tool_calls:
                chars += len(json.dumps(tool_calls))
                
            conv_chars += chars
            
            step_type = step.get("type", "UNKNOWN")
            conv_types[step_type] += chars
            
            # Get specific microstep details
            details = get_step_details(step)
            
            steps_list.append({
                "step_index": step.get("step_index", conv_steps - 1),
                "step_type": step_type,
                "details": details,
                "characters": chars,
                "estimated_tokens": chars // 4
            })
            
            if conv_steps == 1:
                req = step.get("content", "") or ""
                if "Run ONLY Phase" in req:
                    first_prompt = req.split("\n")[0] + " ... " + req.split("\n")[1][:80]
                else:
                    first_prompt = req[:100].replace("\n", " ")
                    
    return {
        "id": conv_id,
        "chars": conv_chars,
        "steps": conv_steps,
        "types": conv_types,
        "prompt": first_prompt,
        "mtime": os.path.getmtime(path),
        "steps_list": steps_list
    }

def main():
    data_dir = load_data_dir()
    jobs_dir = os.path.join(data_dir, "jobs")
    
    # Target job
    target_job_id = sys.argv[1] if len(sys.argv) > 1 else None
    
    job_id, job_dir = get_recent_job(jobs_dir, target_job_id)
    print(f"Target Job: {job_id}")
    print(f"Job Directory: {job_dir}")
    
    # Get time window of this job
    job_dir_mtime = os.path.getmtime(job_dir)
    # The start time is approximate - creation of log/progress files
    start_time = job_dir_mtime - 120 # 2 mins buffer
    end_time = datetime.datetime.now().timestamp()
    
    # Brain directory
    brain_dir = os.path.expanduser("~/.gemini/antigravity-cli/brain")
    
    # 1. Scan from log file
    log_conv_ids = scan_conv_ids_from_log(job_dir)
    
    # 2. Scan by time
    time_conv_ids = find_conv_ids_by_time(brain_dir, start_time, end_time)
    
    # Combined list
    all_conv_ids = log_conv_ids.union(time_conv_ids)
    
    print(f"Found {len(all_conv_ids)} conversations linked to this job run.")
    print("=" * 70)
    
    overall_chars = 0
    overall_steps = 0
    overall_types = defaultdict(int)
    
    analyzed_convs = []
    for cid in all_conv_ids:
        res = analyze_conversation(brain_dir, cid)
        if res:
            analyzed_convs.append(res)
            
    # Sort by modification time
    analyzed_convs.sort(key=lambda x: x["mtime"])
    
    csv_rows = []
    
    for c in analyzed_convs:
        dt = datetime.datetime.fromtimestamp(c["mtime"]).strftime('%Y-%m-%d %H:%M:%S')
        print(f"Conversation: {c['id']} ({dt})")
        print(f"  Prompt: {c['prompt']}")
        print(f"  Total Chars: {c['chars']:,} (~{c['chars']//4:,} tokens)")
        print(f"  Steps: {c['steps']}")
        for k, v in sorted(c["types"].items(), key=lambda x: x[1], reverse=True):
            print(f"    - {k}: {v:,} chars (~{v//4:,} tokens)")
        print("-" * 70)
        
        overall_chars += c["chars"]
        overall_steps += c["steps"]
        for k, v in c["types"].items():
            overall_types[k] += v
            
        # Accumulate rows for CSV
        for step in c["steps_list"]:
            csv_rows.append({
                "job_id": job_id,
                "conversation_id": c["id"],
                "step_index": step["step_index"],
                "step_type": step["step_type"],
                "details": step["details"],
                "characters": step["characters"],
                "estimated_tokens": step["estimated_tokens"]
            })

    # Write to CSV file in job dir
    csv_file_path = os.path.join(job_dir, "token_usage_breakdown.csv")
    fields = ["job_id", "conversation_id", "step_index", "step_type", "details", "characters", "estimated_tokens"]
    
    try:
        with open(csv_file_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fields)
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"\n[Success] Detailed microstep token usage saved to CSV:")
        print(f"  {csv_file_path}")
    except Exception as e:
        print(f"Error writing CSV file: {e}")

    print("\n" + "=" * 70)
    print("JOB TOKEN USAGE SUMMARY")
    print("=" * 70)
    print(f"Total Conversations: {len(analyzed_convs)}")
    print(f"Total Steps Executed: {overall_steps}")
    print(f"Total Character Footprint: {overall_chars:,} chars")
    print(f"Total Estimated Tokens: ~{overall_chars//4:,} tokens")
    print("-" * 70)
    print("Usage by Action / Message Type:")
    for k, v in sorted(overall_types.items(), key=lambda x: x[1], reverse=True):
        print(f"  {k:<20}: {v:>10,} chars (~{v//4:>8,} tokens)")
    print("=" * 70)

if __name__ == "__main__":
    main()
