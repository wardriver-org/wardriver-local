const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('public/dashboard.html','utf8');
const code=html.slice(html.indexOf('function activeTraceRows(){'),html.indexOf("const WEBGL_POINT_SOURCE="));
function element(){return{style:{opacity:'0'},dataset:{},children:[],setAttribute(k,v){this[k]=v},appendChild(v){this.children.push(v)},set innerHTML(v){this.children=[]}}}
const svg=element(),note=element();let frames=[],moving=false;
const c={Map,Number,String,Array,Math,Date,requestAnimationFrame:f=>{frames.push(f);return frames.length},mapStyle:'pointlines',mlReady:true,mlMap:{isMoving:()=>moving},v29MapPageActive:()=>true,activeAppPage:'map',v211TrackRows:[],currentRows:[],mappedLineEl:svg,mapEl:{clientWidth:800,clientHeight:600},colorTraceCollection:false,v210HeavyRenderGeneration:0,document:{createDocumentFragment:element,createElementNS:element,querySelector:()=>note},observationTimeMs:r=>r.t,haversineKm:()=>.01,screenPointFor:(lat,lon)=>({x:lon+200,y:lat+100})};
vm.createContext(c);vm.runInContext(code,c);
const flush=()=>{const jobs=frames;frames=[];jobs.forEach(f=>f())};
c.v211TrackRows=[{source:'drive',latitude:10,longitude:-10,t:1000},{source:'drive',latitude:10.0001,longitude:-10.0001,t:2000}];
c.scheduleMappedLines();c.scheduleMappedLines();assert.equal(frames.length,1);flush();
assert.equal(note.dataset.traceSegments,'1');assert.equal(svg.style.display,'block');assert.equal(svg.style.opacity,'');assert.equal(svg.children[0].children.length,2);
svg.style.opacity='0';moving=true;c.scheduleMappedLines();flush();assert.equal(svg.style.opacity,'');
moving=false;c.scheduleMappedLines();flush();assert.equal(svg.style.opacity,'');
c.mapEl.clientWidth=0;c.scheduleMappedLines();flush();c.mapEl.clientWidth=800;c.scheduleMappedLines();flush();assert.equal(svg.viewBox,'0 0 800 600');
c.mapStyle='points';c.scheduleMappedLines();flush();assert.equal(svg.style.display,'none');
c.mapStyle='lines';c.scheduleMappedLines();flush();assert.equal(svg.style.display,'block');
assert(html.includes("mapStyle='pointlines'"));
for(const event of ['idle','resize','style.load'])assert(html.includes(`mlMap.on('${event}',scheduleMappedLines)`));
assert(html.includes('v211TrackRows=rows;v211TracksTruncated=false;scheduleMappedLines();'));
console.log('PASS: traces render without movement; opacity recovery; coalescing; resize; style toggle; lifecycle/data hooks');
// More than the former 30,000 segment cutoff must retain the tail drive.
c.mapStyle='lines';c.screenPointFor=(lat,lon)=>({x:(lon+118)*2000000,y:lat});
c.v211TrackRows=Array.from({length:31005},(_,i)=>({drive_key:'first',source:'same',latitude:10,longitude:-118+i*.000001,t:1000+i*1000}));
c.v211TrackRows.push({drive_key:'last',source:'same',latitude:10,longitude:-117,t:1000},{drive_key:'last',source:'same',latitude:10,longitude:-116.9999,t:2000});
c.scheduleMappedLines();flush();assert.equal(note.dataset.traceSegments,'31005');assert.equal(svg.children[0].children.length,4);
// A sub-pixel route still retains its endpoints, rather than vanishing.
c.screenPointFor=(lat,lon)=>({x:lon,y:lat});c.v211TrackRows=c.v211TrackRows.slice(-2);c.scheduleMappedLines();flush();assert.equal(note.dataset.traceSegments,'1');
console.log('PASS: routes beyond 30,000 segments, distinct drives with same source, subpixel endpoint preservation');
