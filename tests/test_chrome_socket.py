"""The shipped extension preserves control ordering across async storage."""

import subprocess
from pathlib import Path


def test_extension_request_is_saved_before_automatic_answer():
    source = Path(__file__).parents[1] / "src/aegis/extension/background.js"
    script = r"""
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.includes('  let controls = Promise.resolve();')
  ? source.indexOf('  let controls = Promise.resolve();')
  : source.indexOf('  sock.onmessage =');
const code = source.slice(start, source.indexOf('  sock.onclose =', start));
let stored = [], given = 0, release;
const blocked = new Promise(resolve => {release = resolve;});
const requests = async () => stored;
const setRequests = async rows => {await blocked; stored = rows;};
const give = async id => {if ((await requests()).some(r => r.id === id)) given++;};
const chrome = {runtime:{sendMessage:async()=>{}}};
const sock = {};
let called = false;
const onCall = async () => {called = true;};
new Function('sock','chrome','requests','setRequests','give','onCall',code)(sock,chrome,requests,setRequests,give,onCall);
(async()=>{
  const request = sock.onmessage({data:JSON.stringify({t:'request',request:{id:'rq-one'}})});
  const answer = sock.onmessage({data:JSON.stringify({t:'answer',request:'rq-one',give:true})});
  await sock.onmessage({data:JSON.stringify({t:'call'})});
  if(!called)throw new Error('Browser calls were blocked by control storage');
  await Promise.resolve();
  release();
  await Promise.all([request,answer]);
  if(given!==1)throw new Error('Automatic answer arrived before its request was stored: '+given);
})();
"""
    subprocess.run(
        ["node", "-e", script, str(source)], check=True, capture_output=True, text=True
    )


def test_native_web_eval_activates_background_runtime_without_focusing_a_tab():
    source = Path(__file__).parents[1] / "src/aegis/extension/background.js"
    script = r"""
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('  async eval(tabId, a) {');
const code = source.slice(start, source.indexOf('  async screenshot', start));
const calls=[]; let allowed=true;
const shared=async()=>({1:{eval:allowed}});
const dbg=async(id,method,args)=>{calls.push({id,method,args});return {result:{value:{observed:true}}};};
class Fail extends Error{constructor(code,message){super(message);this.code=code;}}
const runtime=new Function('shared','dbg','Fail','return ({'+code+'});')(shared,dbg,Fail);
(async()=>{
  const result=await runtime.eval(1,{expression:'document.title',web:true});
  if(!result.web_ready||calls.length!==2||calls[0].method!=='Emulation.setFocusEmulationEnabled'||calls[0].args.enabled!==true||calls[1].method!=='Runtime.evaluate')throw new Error('Native page was not activated before observation');
  calls.length=0;
  await runtime.eval(1,{expression:'document.title'});
  if(calls.length!==1||calls[0].method!=='Runtime.evaluate')throw new Error('Ordinary browser scripts changed focus emulation');
  calls.length=0;allowed=false;
  try{await runtime.eval(1,{expression:'document.title',web:true});throw new Error('Scripts refusal missing');}
  catch(error){if(error.code!=='scripts_off')throw error;}
  if(calls.length)throw new Error('Scripts-off reached the browser');
})();
"""
    subprocess.run(
        ["node", "-e", script, str(source)], check=True, capture_output=True, text=True
    )
