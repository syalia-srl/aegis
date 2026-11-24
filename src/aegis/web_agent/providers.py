"""The measured page differences of the four web AI providers.

The shared observer preserves code languages and source links and requires a
completion control on the current answer. An unfamiliar surface stays incomplete.
Selectors are capabilities to verify against the live page, not API guarantees.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    url: str
    input: str
    answers: str
    content: str
    users: str
    done: str
    conversation_prefix: str
    send: str | None = None
    send_key: str | None = None

    def accepts(self, url: str) -> bool:
        target, base = urlsplit(url), urlsplit(self.url)
        return target.scheme == "https" and target.netloc == base.netloc

    def conversation(self, url: str) -> str | None:
        p = urlsplit(url)
        if self.accepts(url) and p.path.startswith(self.conversation_prefix):
            tail = p.path.removeprefix(self.conversation_prefix)
            # ChatGPT replaces this provisional browser-local route with the
            # persisted conversation id after accepting the first submission.
            if self.name == "web-chatgpt" and unquote(tail).startswith(
                "local-chatgpt:"
            ):
                return None
            if tail and tail != "new-chat":
                return f"https://{p.netloc}{p.path}"
        return None

    @property
    def send_ready_script(self) -> str | None:
        return (
            _SEND_READY.replace("__SELECTOR__", json.dumps(self.send))
            if self.send
            else None
        )

    @property
    def observe_script(self) -> str:
        config = {
            "provider": self.name,
            "answers": self.answers,
            "content": self.content,
            "users": self.users,
            "done": self.done,
        }
        return _OBSERVER.replace("__CONFIG__", json.dumps(config))


PROVIDERS = {
    "web-gemini": Provider(
        "web-gemini",
        "Gemini web",
        "https://gemini.google.com/app",
        'role=textbox[name="Enter a prompt for Gemini"]',
        "model-response",
        ".response-content, .markdown",
        "user-query .query-text",
        'button[aria-label="Good response"], button[aria-label="Copy"]',
        "/app/",
    ),
    "web-deepseek": Provider(
        "web-deepseek",
        "DeepSeek web",
        "https://chat.deepseek.com/",
        'textarea[placeholder="Message DeepSeek"]',
        ".ds-markdown",
        "",
        '[data-message-role="user"], .chat-user-message',
        'button[aria-label="Copy"], [role="button"][aria-label="Copy"], [data-testid="copy-button"], [title="Copy"], [role="button"][aria-label="Read aloud"]',
        "/a/chat/s/",
        send=".ds-button--primary.ds-button--circle",
    ),
    "web-qwen": Provider(
        "web-qwen",
        "Qwen web",
        "https://chat.qwen.ai/",
        'role=textbox[name="Ask Qwen"]',
        ".chat-response-message",
        ".phase-answer",
        ".chat-user-message",
        'button[aria-label="Regenerate"]',
        "/c/",
        send='button[aria-label="Send"]',
        send_key="Enter",
    ),
    "web-chatgpt": Provider(
        "web-chatgpt",
        "ChatGPT web",
        "https://chatgpt.com/",
        'role=textbox[name="Ask ChatGPT"]',
        '[data-message-author-role="assistant"], [data-dil-response]',
        "",
        '[data-message-author-role="user"], [data-testid="user-message"]',
        'button[aria-label="Copy"], [data-testid="copy-turn-action-button"]',
        "/c/",
        send='button[aria-label="Send"]',
    ),
}

STOP_SCRIPT = r"""(() => {
  const b = Array.from(document.querySelectorAll('button,[role=button]')).find(e =>
    e.getClientRects().length && /^stop(?: response| generating| streaming)?$/i.test(e.getAttribute('aria-label') || e.innerText.trim()));
  if (!b) return {stopped:false}; b.click(); return {stopped:true};
})()"""

_SEND_READY = """(() => {
  const b=document.querySelector(__SELECTOR__);
  if(!b || !b.getClientRects().length || b.disabled || b.getAttribute('aria-disabled')==='true' || b.classList.contains('ds-button--disabled') || b.classList.contains('disabled')) return {ready:false};
  b.scrollIntoView({behavior:'instant',block:'center',inline:'center'});
  const r=b.getBoundingClientRect();
  return {ready:true,rect:[r.x,r.y,r.width,r.height]};
})()"""

_OBSERVER = r"""(() => {
  const cfg=__CONFIG__;
  const visible=e=>!!e.getClientRects().length;
  const all=Array.from(document.querySelectorAll(cfg.answers));
  const roots=all.filter(e=>!all.some(other=>other!==e && other.contains(e)));
  const controls=Array.from(document.querySelectorAll('button,[role=button]'));
  const busy=controls.some(e=>visible(e)&&/^stop(?: response| generating| streaming)?$/i.test(e.getAttribute('aria-label')||e.innerText.trim()))
    || !!document.querySelector('.qwen-chat-status-card-title-animate');
  function qwenMessage(root,role='assistant'){
    if(cfg.provider!=='web-qwen')return null;
    const key=Object.keys(root).find(k=>k.startsWith('__reactFiber'));
    for(let f=key&&root[key],depth=0;f&&depth<10;f=f.return,depth++){
      const m=f.memoizedProps?.message;
      if(m?.role===role && (role==='user' || 'chat-response-message-'+m.id===root.id))return m;
    }
    return null;
  }
  function completed(root){
    const m=qwenMessage(root);
    if(m?.done===true && Array.isArray(m.content_list)
      && m.content_list.some(p=>p.phase==='answer'&&p.status==='finished'))return true;
    for(let e=root;e&&e!==document.body;e=e.parentElement){
      if(e!==root && roots.filter(r=>e.contains(r)).length>1) break;
      if(Array.from(e.querySelectorAll(cfg.done)).some(visible)) return true;
    }
    return false;
  }
  function identity(root,index){
    for(let e=root;e&&e!==document.body;e=e.parentElement){
      const id=e.getAttribute('data-message-id')||e.getAttribute('data-dil-message-id')||e.getAttribute('data-message-uuid')||(cfg.provider==='web-deepseek'?e.getAttribute('data-virtual-list-item-key'):null);
      if(id) return id;
      if(e===root&&e.id) return e.id;
      if(e!==root && roots.filter(r=>e.contains(r)).length>1) break;
    }
    return 'observed-'+index;
  }
  function markdown(root){
    let valid=true;
    function walk(node){
      if(node.nodeType===Node.TEXT_NODE) return node.textContent;
      if(node.nodeType!==Node.ELEMENT_NODE) return '';
      const tag=node.tagName.toLowerCase();
      if(['button','svg','script','style'].includes(tag)||node.getAttribute('aria-hidden')==='true')return '';
      if(/^Gemini said$/i.test(node.innerText.trim()) && /^h[1-6]$/.test(tag))return '';
      if(tag==='pre'){
        if(node.querySelector('.monaco-editor')){valid=false;return '';}
        const code=node.querySelector('code')||node;
        let language=(code.className.match(/(?:^|\s)language-([\w+-]+)/)||[])[1]||node.dataset.language||node.dataset.lang||'';
        if(!language){
          const block=node.closest('[data-code-block], code-block, .md-code-block, .qwen-markdown-code');
          if(block){
            const header=block.querySelector('[class*="code-header"], [class*="language"]');
            const label=(header?.innerText||block.innerText.split('\n')[0]).trim().toLowerCase();
            if(/^(bash|python|javascript|typescript|json|text|sh|shell)$/.test(label))language=label;
          }
        }
        const text=code.innerText.replace(/\n$/,'');
        if(text.split('\n').find(l=>l.trim())==='# aegis: execute' && !language)valid=false;
        const ticks='`'.repeat(Math.max(3,...Array.from(text.matchAll(/`+/g),m=>m[0].length+1)));
        return '\n\n'+ticks+language+'\n'+text+'\n'+ticks+'\n\n';
      }
      const inside=Array.from(node.childNodes).map(walk).join('');
      if(tag==='a')return '['+inside+']('+node.href+')';
      if(tag==='br')return '\n';
      if(tag==='code')return '`'+inside+'`';
      if(tag==='blockquote')return '\n\n'+inside.trim().split('\n').map(l=>'> '+l).join('\n')+'\n\n';
      if(tag==='li')return '\n- '+inside.trim().replace(/\n/g,'\n  ')+'\n';
      if(/^h[1-6]$/.test(tag))return '\n\n'+'#'.repeat(Number(tag[1]))+' '+inside+'\n\n';
      if(['p','div','section','ul','ol'].includes(tag))return '\n'+inside+'\n';
      return inside;
    }
    return {text:walk(root).trim(),valid};
  }
  const messages=roots.map((root,index)=>{
    const content=cfg.content?root.querySelector(cfg.content):root;
    // Gemini's variants may put the content directly on model-response.
    const answer=content || (cfg.answers==='model-response'?root:null);
    const source=qwenMessage(root)?.content_list?.find(p=>p.phase==='answer')?.content;
    // Monaco's rendered text adds headers, line numbers and NBSPs. Use the
    // provider's exact Markdown source instead of reconstructing that view.
    const parsed=typeof source==='string'?{text:source,valid:true}:answer?markdown(answer):{text:'',valid:false};
    return {id:identity(root,index),markdown:parsed.text,complete:!!answer&&parsed.valid&&completed(root)&&(!busy||index<roots.length-1)};
  });
  const users=Array.from(document.querySelectorAll(cfg.users)).map((e,i)=>{
    const source=qwenMessage(e,'user');
    if(typeof source?.id==='string' && typeof source?.content==='string')return {id:source.id,text:source.content};
    return {id:e.getAttribute('data-message-id')||e.id||(cfg.users==='user-query .query-text'?'query-':'user-')+i,text:e.innerText.trim()};
  });
  const dialog=Array.from(document.querySelectorAll('[role=dialog],dialog')).find(e=>visible(e)&&/confirm your age|sign in|log in|verify|captcha/i.test(e.innerText));
  const signIn=cfg.provider==='web-deepseek' && location.pathname==='/sign_in';
  return {url:location.href,title:document.title,messages,users,busy,partial_history:['web-deepseek','web-chatgpt'].includes(cfg.provider),intervention:signIn?"Sign in to DeepSeek in the backing Chrome tab":dialog?dialog.innerText.slice(0,300):null};
})()"""
