const fs = require('fs'), vm = require('vm'), assert = require('assert');
const elements = Object.fromEntries(['cellTowerToggle','cellTowerFetch','cellTowerStatus'].map(id=>[id,{listeners:{},addEventListener(t,f){this.listeners[t]=f},setAttribute(){}}]));
const handlers={},sources={},layers={};
let calls=0;
const context={console,AbortController,setTimeout,clearTimeout,confirm:()=>true,document:{querySelector:s=>elements[s.slice(1)]},mlMap:{isStyleLoaded:()=>true,getSource:id=>sources[id],addSource(id,s){sources[id]={...s,setData(d){this.data=d}}},getLayer:id=>layers[id],addLayer(l){layers[l.id]=l},setLayoutProperty(id,k,v){layers[id][k]=v},getBounds:()=>({getWest:()=>-10.3,getSouth:()=>10,getEast:()=>-10.1,getNorth:()=>10.2}),on(...args){handlers[args[0]]=args.at(-1)}},fetch:async(url,options)=>{calls++;return{ok:true,json:async()=>({type:'FeatureCollection',features:[],cached:true})}}};
vm.createContext(context);vm.runInContext(fs.readFileSync('public/cell-towers.js','utf8'),context);context.initCellTowers();
(async()=>{
assert.equal(calls,0);
elements.cellTowerToggle.listeners.click();await new Promise(r=>setTimeout(r,10));
assert.equal(calls,1);assert.equal(layers['cell-towers'].visibility,'visible');
await elements.cellTowerFetch.listeners.click();assert.equal(calls,2);
delete layers['cell-towers'];delete sources['cell-towers'];handlers['style.load']();assert(layers['cell-towers']);
elements.cellTowerToggle.listeners.click();assert.equal(layers['cell-towers'].visibility,'none');
handlers.moveend();await new Promise(r=>setTimeout(r,300));assert.equal(calls,2);
console.log('UI controls: default-off, cache load, fetch, style recovery, toggle-off passed');
})().catch(e=>{console.error(e);process.exitCode=1});
