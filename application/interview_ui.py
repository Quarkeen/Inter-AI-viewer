import json

import streamlit as st


def thinking(placeholder, message):
    placeholder.html("""
    <div role="status" aria-live="polite" class="interview-thinking">
      <div class="thinking-bars"><i></i><i></i><i></i><i></i><i></i></div>
      <div><strong>""" + message + """</strong><div class="thinking-hint">Your progress is saved. Please keep this page open.</div></div>
    </div>
    """)


def question_card(question, turn_id, autoplay=True):
    text = json.dumps(question).replace("<", "\\u003c").replace(">", "\\u003e")
    st.iframe(r"""
    <style>
    *{box-sizing:border-box} body{margin:0;font-family:system-ui,sans-serif;color:#eef5f8}
    .card{padding:24px;border:1px solid #2a424b;border-radius:18px;background:linear-gradient(130deg,#152930,#15202b)}
    .label{font-size:11px;letter-spacing:.16em;color:#91cfc4;font-weight:650;margin-bottom:14px}
    #question{font-size:22px;line-height:1.5;font-weight:550;overflow-wrap:anywhere}
    .word{border-radius:4px;transition:background .12s}.speaking{background:#307365;color:#fff}
    .controls{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-top:20px}
    button{cursor:pointer;border:1px solid #46616c;border-radius:8px;background:#213842;color:#eff8fa;padding:9px 13px;font-size:13px}
    button:hover{background:#2d4b56} #status{font-size:12px;color:#a8bbc3}
    </style>
    <div class="card"><div class="label">YOUR INTERVIEWER</div><div id="question" aria-live="polite"></div>
    <div class="controls"><button id="speak" type="button">▶ Read aloud</button>
    <button id="stop" type="button">■ Stop</button><span id="status">Read or listen, then record your answer.</span></div></div>
    <script>
    const text=TEXT_VALUE;
    const container=document.getElementById('question');
    const status=document.getElementById('status');
    const spans=[];let position=0;let utterance=null;
    for(const word of text.match(/\S+\s*/g)||[text]){
      const span=document.createElement('span');span.className='word';span.textContent=word;
      spans.push({span,start:position,end:position+word.length});container.appendChild(span);position+=word.length;
    }
    function clear(){for(const item of spans)item.span.classList.remove('speaking');}
    function stop(){if('speechSynthesis'in window)window.speechSynthesis.cancel();clear();status.textContent='Read or listen, then record your answer.';}
    function speak(){
      if(!('speechSynthesis'in window)){status.textContent='Read-aloud is unavailable in this browser.';return;}
      stop();utterance=new SpeechSynthesisUtterance(text);utterance.lang='en-US';utterance.rate=.95;
      const voices=window.speechSynthesis.getVoices();
      utterance.voice=voices.find(v=>v.localService&&v.lang.startsWith('en'))||voices.find(v=>v.lang.startsWith('en'))||null;
      utterance.onstart=()=>{status.textContent='Speaking…';};
      utterance.onboundary=event=>{clear();const item=spans.find(item=>item.start<=event.charIndex&&item.end>event.charIndex);if(item)item.span.classList.add('speaking');};
      utterance.onend=()=>{clear();status.textContent='Your turn. Record your answer below.';};
      utterance.onerror=()=>{clear();status.textContent='Select Read aloud to enable speech, or read the question above.';};
      window.speechSynthesis.speak(utterance);
    }
    document.getElementById('speak').onclick=speak;document.getElementById('stop').onclick=stop;
    window.addEventListener('pagehide',stop);
    let shouldAutoplay=AUTO_VALUE;
    try{const key='interview-spoken-'+TURN_ID;if(sessionStorage.getItem(key))shouldAutoplay=false;else if(shouldAutoplay)sessionStorage.setItem(key,'1');}catch(e){}
    if(shouldAutoplay)setTimeout(speak,250);
    </script>
    """.replace("AUTO_VALUE", "true" if autoplay else "false").replace("TURN_ID", json.dumps(turn_id)).replace("TEXT_VALUE", text), height="content", alt="Interview question and read-aloud controls")


ROOM_CSS = """
<style>
.interview-eyebrow{font-size:.75rem;letter-spacing:.14em;color:#65baa8;font-weight:650;margin-bottom:8px}
.interview-thinking{display:flex;gap:18px;align-items:center;padding:22px;border:1px solid #315247;border-radius:14px;background:rgba(62,140,118,.08);margin:12px 0}
.thinking-hint{font-size:.82rem;opacity:.65;margin-top:5px}
.thinking-bars{display:flex;align-items:center;gap:4px;height:32px;min-width:36px}
.thinking-bars i{display:block;width:4px;height:12px;background:#6cc6ac;border-radius:4px;animation:interview-wave 1s ease-in-out infinite}
.thinking-bars i:nth-child(2){animation-delay:.12s}.thinking-bars i:nth-child(3){animation-delay:.24s}.thinking-bars i:nth-child(4){animation-delay:.36s}.thinking-bars i:nth-child(5){animation-delay:.48s}
@keyframes interview-wave{0%,100%{height:8px;opacity:.4}50%{height:30px;opacity:1}}
@media(prefers-reduced-motion:reduce){.thinking-bars i{animation:none;height:20px}}
div[data-testid="stAudioInput"]{border:1px solid rgba(110,165,155,.3);border-radius:14px;padding:10px}
</style>
"""
