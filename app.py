from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def home():
    # 파이썬에서 UI(HTML)로 보낼 데이터 정의
    message_to_ui = "백엔드에서 보낸 실시간 데이터입니다!"
    
    # templates/index.html 파일을 읽어서 화면에 띄우고, 변수를 전달합니다.
    return render_template('index.html', user_msg=message_to_ui)

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True
