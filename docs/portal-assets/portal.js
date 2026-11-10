document.querySelector('.theme')?.addEventListener('click',()=>{const r=document.documentElement; r.dataset.theme=r.dataset.theme==='dark'?'light':'dark';try{localStorage.setItem('mitex-theme',r.dataset.theme)}catch(e){}});
document.querySelectorAll('.filter-scope').forEach(scope=>{
 const cards=[...scope.querySelectorAll('.filter-item')], search=scope.querySelector('input[type=search]'), selects=[...scope.querySelectorAll('select[data-field]')], more=scope.querySelector('.more'), count=scope.querySelector('.result-count'), empty=scope.querySelector('.empty');
 const step=Number(scope.dataset.limit)||24;let limit=step;
 function update(reset=false){if(reset)limit=step;const q=(search?.value||'').trim().toLocaleLowerCase('ru');let matches=0,shown=0;
 for(const card of cards){const ok=(!q||(card.dataset.search||'').includes(q))&&selects.every(s=>!s.value||(card.dataset[s.dataset.field]||'').split(' | ').includes(s.value));const show=ok&&matches++<limit;card.hidden=!show;if(show)shown++;}
 if(count)count.textContent=`Найдено: ${matches}. Показано: ${shown}.`;if(more)more.hidden=matches<=limit;if(empty)empty.hidden=matches>0;}
 search?.addEventListener('input',()=>update(true));selects.forEach(s=>s.addEventListener('change',()=>update(true)));more?.addEventListener('click',()=>{limit+=step;update()});scope.querySelector('.reset')?.addEventListener('click',()=>{if(search)search.value='';selects.forEach(s=>s.value='');update(true)});update();
});
document.querySelectorAll('[data-telegram]').forEach(button=>button.addEventListener('click',()=>{const id=button.dataset.telegram;if(!/^[A-Za-z][A-Za-z0-9_]+\/\d+$/.test(id))return;const box=document.createElement('div'),script=document.createElement('script');script.src='https://telegram.org/js/telegram-widget.js?22';script.async=true;script.dataset.telegramPost=id;script.dataset.width='100%';box.appendChild(script);button.replaceWith(box)}));
