"""Редакторский реестр: только заявленные продукты, охват всех участников."""
import csv,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
folder=ROOT/'data/site'
records=json.loads((folder/'exhibitors.json').read_text(encoding='utf-8'))
NEWS='https://mitexpo.ru/press/novosti/'
# Тексты написаны по прочитанным первоисточникам; LLM API не использовался.
PRODUCTS=[
('84624','PLANTIC','Сверхлёгкая штыковая лопата','Анонсирован новый вариант садовой лопаты. Масса и артикул в сообщении организатора не указаны.','14232',True),
('84624','PLANTIC','Телескопический скребок Plantic snow','Инструмент для удаления снега с крыши с раздвижной ручкой.','14232',True),
('84624','PLANTIC','Автомобильные щётки Plantic auto / light','Две линейки щёток с интегрированным скребком для ухода за автомобилем зимой.','14232',True),
('84624','PLANTIC','Лопата серии «Оружейная сталь»','Усиленная штыковая модель названа среди новинок предстоящей экспозиции.','14232',True),
('','ELITECH','Аккумуляторная платформа ELP','Совместимая экосистема 20/40 В: заявлено более 100 моделей. Среди них CD 20SL2, CS 2012BLPK, CCS 2050BL и CAG 2012BL.','14147',True),
('84646','VILLARTEC / DEVON','VILLARTEC MI 1500V','Робот-газонокосилка для площади до 1500 м², с камерой и 3D-лидаром для построения карты участка.','14197',True),
('84646','VILLARTEC / DEVON','Газонокосилки Zero-Turn Hydro','Линейка с независимыми гидроприводами колёс, включая машины с сиденьем и платформой оператора.','14197',True),
('84646','VILLARTEC / DEVON','Снегоуборщики SnowBoss','Новая премиальная линейка с гидростатической трансмиссией Hydro Drive.','14197',True),
('84646','VILLARTEC / DEVON','DEVON: платформы 12V и 20V','Заявлены аккумуляторные инструменты, бесщёточные сетевые УШМ и автономные нейлеры для бетона.','14197',True),
('84488','A-iPower','Портативные станции и многотопливные генераторы','Анонсированы станции с аккумуляторами LiFePO₄, двухтопливные и трёхтопливные инверторные генераторы. Точные модели в релизе не названы.','14196',True),
('84582','CAIMAN','Роботы и аккумуляторная техника','В экспозиции заявлены роботы-газонокосилки, роботы-мульчеры, ручной садовый инструмент и аккумуляторные решения для профессионального ухода.','14210',True),
('130394','TecEner','Обновлённая линейка генераторов','Заявлены изменения оснащения: USB-C, увеличенные баки рамных моделей и кнопочный запуск; расширение бензиновой, дизельной и двухтопливной линейки.','14220',True),
('84542','ИНТЕРСКОЛ','Бесщёточные двигатели и строительные пылесосы','Организатор сообщает о планируемой презентации этих направлений. Модели и параметры в публикации не уточнены.','14220',True),
('135005','ВсеИнструменты.ру / INFORCE','Электроинструмент INFORCE','Запланирован публичный показ линейки, выпущенной в 2026 году, и демонстрация работы инструмента на гоночном автомобиле.','14220',True),
('154305','Крепеж СВ','Крепёж, канаты и монтажная химия','Заявлены три направления: крепёж под СТМ заказчика, канаты SV и монтажная химия «СВОЙ»/SV. Новизна конкретных моделей не заявлена.','14190',False),
('84632','PROFIPOWER','Инструмент для бытовых и профессиональных задач','Участие и направления экспозиции подтверждены анонсом организатора; конкретные новые модели не перечислены.','14220',False),
]
items=[]
for n,(id,company,title,text,source,novelty) in enumerate(PRODUCTS,1):
    if company=='ELITECH':
        id=next((x['id'] for x in records if 'elitech.ru' in x['website']),id)
    items.append({'id':'announcement-'+str(n),'exhibitor_id':id,'company':company,'title':title,'description':text,'source_url':NEWS+source+'/',
        'status':'Анонс 2026' if novelty else 'Заявленная экспозиция','novelty_claimed':novelty,'shown_at_exhibition':False})
for x in records:
    if x['name'].startswith('FELISATTI'):
        items.append({'id':'felisatti-catalog','exhibitor_id':x['id'],'company':x['name'],'title':'HD и аккумуляторная M-series 18V',
            'description':'В каталоге заявлены серии HD и M-series, а также оснастка, садовая и силовая техника. Конкретные модели не названы.',
            'source_url':x['source_url'],'status':'Анонс в каталоге','novelty_claimed':True,'shown_at_exhibition':False})
by_id={}
for item in items:
    if item['exhibitor_id']:by_id.setdefault(item['exhibitor_id'],[]).append(item['id'])
registry=[]
for x in records:
    ids=by_id.get(x['id'],[])
    registry.append({'id':x['id'],'name':x['name'],'stands':x['stands'],'source_url':x['source_url'],'website':x['website'],
        'status':'Есть опубликованный анонс' if ids else 'Конкретные новинки не подтверждены найденными источниками','item_ids':ids})
(folder/'items.json').write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')
(folder/'novelty_registry.json').write_text(json.dumps(registry,ensure_ascii=False,indent=2),encoding='utf-8')
with (folder/'novelty_registry.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.writer(f,delimiter=';');w.writerow(['Компания','Стенд','Статус новинок','Анонсы','Источник'])
    for x in registry:w.writerow([x['name'],', '.join(x['stands']),x['status'],', '.join(x['item_ids']),x['source_url']])
print('NOVELTIES',len(items),'announcements;',len(by_id),'linked exhibitors;',len(registry),'registry entries')
