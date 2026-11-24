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
