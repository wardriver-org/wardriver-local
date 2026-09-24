const fs=require('fs'),vm=require('vm'),assert=require('assert');
const s=fs.readFileSync('public/dashboard.html','utf8');
const code=s.slice(s.indexOf('function v216DecodeCompact('),s.indexOf('\n}',s.indexOf('function v216DecodeCompact('))+2)+'\n'+s.slice(s.indexOf('async function loadViewportTracks('),s.indexOf('async function v29LoadViewport('));
let calls=0,renders=0;
const c={URLSearchParams,DOMException,collectionFilter:'all',v29MapFetchSeq:1,v211TrackRows:[],scheduleMappedLines:()=>renders++,fetch:async url=>{calls++;if(calls===2)assert(url.includes('cursor='));return{ok:true,json:async()=>({ok:true,compact:true,fields:['id','drive_key','latitude','longitude'],rows:[[calls,'drive'+calls,10,10]],has_more:calls===1,next_cursor:calls===1?'next':null})}}};
vm.createContext(c);vm.runInContext(code,c);
(async()=>{const result=await c.loadViewportTracks({west:9,east:11,south:9,north:11},{signal:{aborted:false}},1);assert.equal(calls,2);assert.equal(result.tracks.length,2);assert.equal(renders,1);assert.equal(result.truncated,false);await assert.rejects(()=>c.loadViewportTracks({},{signal:{aborted:true}},1),{name:'AbortError'});console.log('PASS: compact pagination, atomic rendering, stale-request protection')})().catch(e=>{console.error(e);process.exitCode=1});
