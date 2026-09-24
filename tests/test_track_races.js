const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('public/dashboard.html','utf8');
const code=html.slice(html.indexOf('async function loadViewportTracks('),html.indexOf('async function v29LoadViewport('));
const pending=[];let renders=0;
const ctx={URLSearchParams,DOMException,collectionFilter:'all',v29MapFetchSeq:1,v211TrackRows:[{id:'previous-complete'}],v216DecodeCompact:p=>p,scheduleMappedLines:()=>renders++,fetch:()=>new Promise((resolve,reject)=>pending.push({resolve,reject}))};
vm.createContext(ctx);vm.runInContext(code,ctx);
const bounds={west:0,east:1,south:0,north:1};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const page=(rows,next=null)=>({ok:true,json:async()=>({ok:true,tracks:rows,has_more:!!next,next_cursor:next})});
(async()=>{
 const ctrl=new AbortController();
 const old=ctx.loadViewportTracks(bounds,ctrl,1);const oldCheck=assert.rejects(old,{name:'AbortError'});
 pending.shift().resolve(page([{id:'old-page-1'}],'cursor'));await tick();
 assert.equal(ctx.v211TrackRows[0].id,'previous-complete');assert.equal(renders,0);
 // Supersede the viewport while its second page is still in flight.
 ctx.v29MapFetchSeq=2;ctrl.abort();const newer=ctx.loadViewportTracks(bounds,new AbortController(),2);
 pending.pop().resolve(page([{id:'new-complete'}]));await newer;
 assert.equal(ctx.v211TrackRows[0].id,'new-complete');assert.equal(renders,1);
 pending.shift().resolve(page([{id:'old-page-2'}]));await oldCheck;
 assert.equal(ctx.v211TrackRows[0].id,'new-complete');assert.equal(renders,1);
 // A later page failure leaves the last good snapshot untouched.
 ctx.v29MapFetchSeq=3;const failed=ctx.loadViewportTracks(bounds,new AbortController(),3);const failureCheck=assert.rejects(failed,/offline/);
 pending.shift().resolve(page([{id:'incomplete'}],'cursor'));await tick();
 pending.shift().reject(Error('offline'));await failureCheck;
 assert.equal(ctx.v211TrackRows[0].id,'new-complete');assert.equal(renders,1);
 // A complete empty response intentionally clears old routes.
 ctx.v29MapFetchSeq=4;const empty=ctx.loadViewportTracks(bounds,new AbortController(),4);
 pending.shift().resolve(page([]));await empty;assert.equal(ctx.v211TrackRows.length,0);assert.equal(renders,2);
 console.log('PASS: delayed pages, atomic snapshots, superseded viewport, page failure, empty viewport');
})().catch(e=>{console.error(e);process.exitCode=1});
