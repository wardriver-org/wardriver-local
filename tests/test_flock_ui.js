const fs=require('fs'),vm=require('vm'),assert=require('assert');
const s=fs.readFileSync('public/dashboard.html','utf8');const c={};vm.createContext(c);
vm.runInContext(s.slice(s.indexOf('function isFlockObservation('),s.indexOf('function activeTraceRows(')),c);
assert(!c.isFlockObservation({name:'Flock Safety',bssid:'70:C9:4E:00:00:01'}));
assert(!c.isFlockObservation({is_flock:0,flock_identity_signal:1,flock_score:99}));
assert(c.isFlockObservation({is_flock:1,flock_identity_signal:1,flock_score:80}));
assert(!c.isFlockObservation({is_flock:1,flock_identity_signal:1,flock_score:99,flock_review_decision:'rejected'}));
assert(c.isFlockObservation({flock_review_decision:'confirmed'}));console.log('Flock UI classification tests passed');
