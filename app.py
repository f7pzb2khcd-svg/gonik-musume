from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re
import string
import random
import time

app = Flask(__name__)
CORS(app)

ROOMS_DB = {}

def generate_room_id(length=6):
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def extract_racers(url):
    if "m.dcinside.com" in url:
        url = url.replace("m.dcinside.com/board", "gall.dcinside.com/board/view")
        url = url.replace("m.dcinside.com/mini", "gall.dcinside.com/mini/board/view")
        url = url.replace("m.dcinside.com/mgallery", "gall.dcinside.com/mgallery/board/view")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": url
    }
    session = requests.Session()
    
    try:
        res = session.get(url, headers=headers, timeout=5)
        res.raise_for_status()
    except Exception as e:
        return [], f"게시글 접속 오류: {e}"
        
    parsed_url = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed_url.query)
    gall_id = qs.get('id', [None])[0]
    gall_no = qs.get('no', [None])[0]
    
    if not gall_id or not gall_no:
        return [], "URL에서 id와 no를 찾을 수 없습니다."
        
    e_s_n_o = ""
    soup = BeautifulSoup(res.text, 'html.parser')
    token_input = soup.find('input', {'id': 'e_s_n_o'})
    if token_input:
        e_s_n_o = token_input.get('value')
    else:
        match = re.search(r'var\s+e_s_n_o\s*=\s*"([^"]+)"', res.text)
        if match: e_s_n_o = match.group(1)

    gall_type = "G"
    if "/mgallery/" in url: gall_type = "M"
    elif "/mini/" in url: gall_type = "MI"

    ajax_url = "https://gall.dcinside.com/board/comment/"
    ajax_headers = headers.copy()
    ajax_headers["X-Requested-With"] = "XMLHttpRequest"
    
    payload = {
        "id": gall_id, "no": gall_no, "cmt_id": gall_id, "cmt_no": gall_no,
        "e_s_n_o": e_s_n_o, "_GALLTYPE_": gall_type, "page": 1
    }
    
    try:
        cmt_res = session.post(ajax_url, data=payload, headers=ajax_headers)
        cmt_data = cmt_res.json()
    except Exception as e:
        return [], f"댓글 API 로드 실패: {e}"
        
    racers = set()
    if not cmt_data or "comments" not in cmt_data or not cmt_data["comments"]:
        return [], "댓글이 없거나 불러오지 못했습니다."
        
    for cmt in cmt_data["comments"]:
        if not isinstance(cmt, dict): continue
        uid = cmt.get("user_id", "")
        nick = cmt.get("name", "ㅇㅇ")
        if not uid: continue
        racers.add(f"{nick}({uid})")
        
    return list(racers), None

# 📡 [신규 API] 방을 만들기 전, 순수하게 참가자 명단만 긁어오는 API
@app.route('/api/extract_only', methods=['POST'])
def extract_only():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({"success": False, "message": "URL이 필요합니다."}), 400
    participants, error = extract_racers(url)
    if error:
        return jsonify({"success": False, "message": error}), 400
    return jsonify({"success": True, "participants": participants})

# 📡 [수정된 API] 프론트에서 최종 필터링된 참가자 명단을 받아 방을 개설하는 API
@app.route('/api/create_room_final', methods=['POST'])
def create_room_final():
    data = request.json
    url = data.get('url')
    participants = data.get('participants') # 필터링된 리스트
    scheduled_time = data.get('scheduled_time') # 유닉스 타임스탬프
    
    if not url or not participants or not scheduled_time:
        return jsonify({"success": False, "message": "필수 데이터가 누락되었습니다."}), 400
        
    if len(participants) < 2:
        return jsonify({"success": False, "message": "최소 2명 이상의 주자가 필요합니다."}), 400
        
    room_id = generate_room_id()
    race_seed = f"SEED_{room_id}_{int(time.time())}"
    
    ROOMS_DB[room_id] = {
        "url": url,
        "participants": participants,
        "seed": race_seed,
        "scheduled_time": scheduled_time,
        "created_at": int(time.time())
    }
    
    return jsonify({"success": True, "room_id": room_id})

@app.route('/api/room/<room_id>', methods=['GET'])
def get_room(room_id):
    room_data = ROOMS_DB.get(room_id.upper())
    if not room_data:
        return jsonify({"success": False, "message": "존재하지 않는 방입니다."}), 404
    return jsonify({"success": True, "data": room_data})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
