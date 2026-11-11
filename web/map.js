'use strict';
(() => {
 const $=id=>document.getElementById(id), scheme=window.MITEX_SCHEME;
 const id=new URLSearchParams(location.search).get('company');
 const company=window.MITEX_DATA.exhibitors.find(x=>String(x.id)===id);
 const parts=scheme.companies[id]||[];
 if(!company){$('map-info').textContent='Участник не найден. Вернитесь в каталог или откройте полную схему MITEX.';return;}
 $('map-title').textContent=company.name;document.title=company.name+' · Стенд MITEX 2026';
 const a=document.createElement('a');a.textContent='Официальная карточка компании ↗';a.href=company.source_url;a.target='_blank';a.rel='noopener noreferrer';$('map-links').prepend(a);
 if(!parts.length){$('map-info').textContent='Для этой компании в сохранённой схеме не найдено размещение. Проверьте полную интерактивную схему организатора.';return;}
 $('map-section').hidden=false;
 const hallIds=[...new Set(parts.map(p=>p.hall))];
 for(const hid of hallIds){const o=document.createElement('option');o.value=hid;o.textContent=scheme.halls[hid].name;$('map-hall').append(o);}
 const ns='http://www.w3.org/2000/svg';let svg,group,box,drag;
 function setBox(b){box=b;svg.setAttribute('viewBox',box.join(' '));}
 function focus(){const b=group.getBBox(),pad=Math.max(b.width,b.height)*1.5+25;setBox([b.x-pad,b.y-pad,b.width+pad*2,b.height+pad*2]);}
 function zoom(factor){const [x,y,w,h]=box;if(w*factor<15||w*factor>6000)return;setBox([x+w*(1-factor)/2,y+h*(1-factor)/2,w*factor,h*factor]);}
 function draw(){const hid=$('map-hall').value,hall=scheme.halls[hid];svg=document.createElementNS(ns,'svg');svg.setAttribute('role','img');svg.setAttribute('aria-label',company.name+' — '+hall.name+', выделенный стенд');
  const image=document.createElementNS(ns,'image');image.setAttribute('href',hall.image);for(const [k,v] of Object.entries({x:0,y:0,width:hall.viewBox[2],height:hall.viewBox[3]}))image.setAttribute(k,v);svg.append(image);
  group=document.createElementNS(ns,'g');for(const p of parts.filter(p=>p.hall===hid)){const path=document.createElementNS(ns,'path');path.setAttribute('d',p.d);path.setAttribute('fill','#00c9dc77');path.setAttribute('stroke','#006773');path.setAttribute('stroke-width','3');path.setAttribute('vector-effect','non-scaling-stroke');group.append(path);}svg.append(group);$('map-canvas').replaceChildren(svg);
  $('map-info').textContent=hall.name+(company.stands.length?' · Стенд '+company.stands.join(', '):' · Размещение на схеме');focus();
  svg.onpointerdown=e=>{if(e.button!==0)return;drag={x:e.clientX,y:e.clientY,b:[...box],m:svg.getScreenCTM().inverse()};svg.setPointerCapture(e.pointerId);};
  svg.onpointermove=e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;setBox([drag.b[0]-dx*drag.m.a,drag.b[1]-dy*drag.m.d,drag.b[2],drag.b[3]]);};
  svg.onpointerup=svg.onpointercancel=()=>drag=null;
 }
 $('map-hall').onchange=draw;$('map-focus').onclick=focus;$('map-all').onclick=()=>setBox([...scheme.halls[$('map-hall').value].viewBox]);$('map-plus').onclick=()=>zoom(.7);$('map-minus').onclick=()=>zoom(1/.7);draw();
})();
