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
    gall_id = None
    gall_no = None

    # 1. 🔍 URL에서 갤러리 ID(id)와 글 번호(no) 완벽 추출 (파라미터 or 모바일 경로 호환)
    parsed_url = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed_url.query)
    
    if 'id' in qs and 'no' in qs:
        # 데스크탑 링크 (예: ?id=game&no=123)
        gall_id = qs['id'][0]
        gall_no = qs['no'][0]
    else:
        # 모바일 링크 (예: m.dcinside.com/board/game/123)
        match = re.search(r'/(?:board|mini|mgallery)/([^/?]+)/([^/?]+)', url)
        if match:
            gall_id = match.group(1)
            gall_no = match.group(2)
            
    if not gall_id or not gall_no:
        return [], "URL에서 게시판 ID와 글 번호를 찾을 수 없습니다. 올바른 링크인지 확인해주세요."

    # 2. 🖥️ 무조건 데스크탑 표준 링크로 강제 변환 (크롤링 안정성 확보)
    target_url = url
    if "m.dcinside.com" in url:
        target_url = f"https://gall.dcinside.com/board/view/?id={gall_id}&no={gall_no}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://gall.dcinside.com/"
    }
    session = requests.Session()
    
    try:
        res = session.get(target_url, headers=headers, timeout=5)
        res.raise_for_status()
        
        # 3. 🚨 디시인사이드 특유의 JS 리다이렉트 방어 (일반 URL로 마이너 갤러리 접근 시 튕기는 현상 해결)
        if "location.replace" in res.text:
            redirect_match = re.search(r"location\.replace\(['\"]([^'\"]+)['\"]\)", res.text)
            if redirect_match:
                target_url = redirect_match.group(1)
                # 상대 경로일 경우 도메인 붙여주기
                if target_url.startswith('/'):
                    target_url = "https://gall.dcinside.com" + target_url
                res = session.get(target_url, headers=headers, timeout=5)
                res.raise_for_status()
                
    except Exception as e:
        return [], f"게시글 접속 오류: {e}"
        
    # 4. 🔑 보안 토큰(e_s_n_o) 및 갤러리 타입(일반/마이너/미니) 최종 판별
    e_s_n_o = ""
    soup = BeautifulSoup(res.text, 'html.parser')
    token_input = soup.find('input', {'id': 'e_s_n_o'})
    if token_input:
        e_s_n_o = token_input.get('value')
    else:
        match = re.search(r'var\s+e_s_n_o\s*=\s*"([^"]+)"', res.text)
        if match: e_s_n_o = match.group(1)

    final_url = res.url
    gall_type = "G"
    if "mgallery" in target_url or "mgallery" in final_url: 
        gall_type = "M"
    elif "mini" in target_url or "mini" in final_url: 
        gall_type = "MI"

    # 5. 💬 비밀 댓글 API 호출
    ajax_url = "https://gall.dcinside.com/board/comment/"
    ajax_headers = headers.copy()
    ajax_headers["X-Requested-With"] = "XMLHttpRequest"
    ajax_headers["Referer"] = final_url
    
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
        return [], "이 게시글에는 고닉/반고닉 댓글이 없습니다."
        
    for cmt in cmt_data["comments"]:
        if not isinstance(cmt, dict): continue
        uid = cmt.get("user_id", "")
        nick = cmt.get("name", "ㅇㅇ")
        if not uid: continue
        racers.add(f"{nick}({uid})")
        
    return list(racers), None

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

@app.route('/api/create_room_final', methods=['POST'])
def create_room_final():
    data = request.json
    url = data.get('url')
    participants = data.get('participants') 
    scheduled_time = data.get('scheduled_time') 
    map_type = data.get('map_type', 'short') # 기본값 short (단거리)
    
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
        "map_type": map_type,
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
