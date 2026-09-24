(() => {
 let timer;
 const el = id => document.getElementById(id);
 async function poll(){
  clearTimeout(timer);
  try{
   const r=await fetch('/api/wigle/own-sync',{cache:'no-store'}),j=await r.json();
   if(!r.ok||!j.ok)throw Error(j.error||'Could not read sync status');
   el('wigleOwnSync').disabled=j.running;
   el('wigleOwnStatus').textContent=`${j.account ? j.account+' · ' : ''}${j.phase} · ${j.files} logs · ${j.imported} added · ${j.duplicates} duplicates skipped${j.error?' · '+j.error:''}`;
   if(j.running)timer=setTimeout(poll,1500);
  }catch(e){el('wigleOwnStatus').textContent=e.message;el('wigleOwnSync').disabled=false;}
 }
 el('wigleOwnSync').addEventListener('click',async()=>{
  const send=el('wigleOwnSend').checked,source=send?el('wigleSource').value:'';
  if(send&&!source){el('wigleOwnStatus').textContent='Select your local import to upload first.';return;}
  el('wigleOwnSync').disabled=true;
  try{
   const r=await fetch('/api/wigle/own-sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source,own_data:send})}),j=await r.json();
   if(!r.ok||!j.ok)throw Error(j.error||'Could not start sync');await poll();
  }catch(e){el('wigleOwnStatus').textContent=e.message;el('wigleOwnSync').disabled=false;}
 });
 poll();
})();
