@echo off
cd /d C:\Users\heejun\Desktop\label_crawler

echo [%date% %time%] LABEL 자동 갱신 시작 >> update_log.txt

py -3.11 crawler.py >> update_log.txt 2>&1

py -3.11 -c "
import requests, json
from pathlib import Path

SUPABASE_URL = 'https://cpabezexbhcjfvxmgavz.supabase.co'
SUPABASE_KEY = 'sb_publishable_oB4lkE6rInHgW_wExgmyJQ_OrQe1ZxD'
HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=merge-duplicates',
}

for table in ['clicks','reviews','store_links','products']:
    requests.delete(f'{SUPABASE_URL}/rest/v1/{table}?id=gte.0', headers=HEADERS)
" >> update_log.txt 2>&1

py -3.11 upload_to_supabase.py >> update_log.txt 2>&1

echo [%date% %time%] 갱신 완료 >> update_log.txt
