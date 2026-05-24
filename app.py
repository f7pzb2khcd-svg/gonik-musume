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

# 🎯 디시인사이드 숨겨진 댓글 API를 호출하는 크롤링 2.0 엔진
def extract_racers(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": url
    }
    
    session = requests.Session()
    
    try:
        # 1. 껍데기 페이지에 먼저 접속해서 '보안 토큰'을 가져옵니다.
        res = session.get(url, headers=headers, timeout=5)
        res.raise_for_status()
    except Exception as e:
        return [], f"게시글 접속 오류: {e}"
        
    # 2. URL에서 갤러리 ID와 게시글 번호 추출
    parsed_url = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed_url.query)
    gall_id = qs.get('id', [None])[0]
    gall_no = qs.get('no', [None])[0]
    
    if not gall_id or not gall_no:
        return [], "URL에서 갤러리 ID(id)와 글 번호(no)를 찾을 수 없습니다."
        
    # 3. 디시인사이드 CSRF 방어 토큰(e_s_n_o) 파싱
    e_s_n_o = ""
    soup = BeautifulSoup(res.text, 'html.parser')
    token_input = soup.find('input', {'id': 'e_s_n_o'})
    if token_input:
        e_s_n_o = token_input.get('value')
    else:
        match = re.search(r'var\s+e_s_n_o\s*=\s*"([^"]+)"', res.text)
        if match:
            e_s_n_o = match.group(1)

    # 4. 갤러리 타입 판별 (일반/마이너/미니)
    gall_type = "G"
    if "/mgallery/" in url:
        gall_type = "M"
    elif "/mini/" in url:
        gall_type = "MI"

    # 5. 브라우저인 척 위장하여 '숨겨진 댓글 전용 API' 강제 호출
    ajax_url = "https://gall.dcinside.com/board/comment/"
    ajax_headers = headers.copy()
    ajax_headers["X-Requested-With"] = "XMLHttpRequest" # AJAX 요청 위장
    
    payload = {
        "id": gall_id,
        "no": gall_no,
        "cmt_id": gall_id,
        "cmt_no": gall_no,
        "e_s_n_o": e_s_n_o,
        "_GALLTYPE_": gall_type,
        "page": 1 # 1페이지 댓글 (보통 100개까지 한 번에 로드됨)
    }
    
    try:
        cmt_res = session.post(ajax_url, data=payload, headers=ajax_headers)
        cmt_data = cmt_res.json() # HTML 껍데기가 아닌 순수 JSON 데이터를 받아옴
    except Exception as e:
        return [], f"댓글 API 로드 실패: {e}"
        
    racers = set()
    
    if not cmt_data or "comments" not in cmt_data or not cmt_data["comments"]:
        return [], "해당 글에 댓글이 없거나 데이터를 불러오지 못했습니다."
        
    # 6. JSON 데이터에서 고닉/반고닉만 쏙쏙 추출
    for cmt in cmt_data["comments"]:
        if not isinstance(cmt, dict):
            continue
            
        uid = cmt.get("user_id", "")
        nick = cmt.get("name", "ㅇㅇ")
        
        # user_id(uid)가 없으면 100% 순수 유동(ㅇㅇ 등)이므로 가차없이 버림
        if not uid:
            continue
            
        # 고닉/반고닉을 "닉네임(아이디)" 형태로 저장
        racers.add(f"{nick}({uid})")
        
    return list(racers), None

# ---------------------------------------------------------
# API 라우팅 로직
# ---------------------------------------------------------
@app.route('/api/create_room', methods=['POST'])
def create_room():
    data = request.json
    url = data.get('url')
    scheduled_time = data.get('scheduled_time') 
    
    if not url or not scheduled_time:
        return jsonify({"success": False, "message": "URL과 예약 시간이 필요합니다."}), 400
        
    participants, error = extract_racers(url)
    
    if error:
        return jsonify({"success": False, "message": error}), 400
        
    if len(participants) < 2:
        return jsonify({"success": False, "message": f"고닉/반고닉 댓글이 부족합니다. (현재 {len(participants)}명 / 최소 2명 필요)"}), 400
    
    room_id = generate_room_id()
    race_seed = f"SEED_{room_id}_{int(time.time())}"
    
    ROOMS_DB[room_id] = {
        "url": url,
        "participants": participants,
        "seed": race_seed,
        "scheduled_time": scheduled_time,
        "created_at": int(time.time())
    }
    
    return jsonify({
        "success": True,
        "room_id": room_id,
        "message": f"방이 생성되었습니다! (실제 참가자 {len(participants)}명 추출 완료)"
    })

@app.route('/api/room/<room_id>', methods=['GET'])
def get_room(room_id):
    room_data = ROOMS_DB.get(room_id.upper())
    
    if not room_data:
        return jsonify({"success": False, "message": "존재하지 않거나 만료된 방입니다."}), 404
        
    return jsonify({
        "success": True,
        "data": room_data
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)