[out:json][timeout:300];
(
 relation["type"="route"]["route"~"^(subway|light_rail|train|monorail)$"]["network"~"수도권 전철"](37.0,126.3,38.0,127.8);
 relation["type"="route"]["ref"="AREX"](37.0,126.3,38.0,127.8);
 relation["type"="route"]["ref"="W"](37.0,126.3,38.0,127.8);
)->.r;
.r out body;
node(r.r);
out body;
