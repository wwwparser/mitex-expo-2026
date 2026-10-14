import json,re
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1];folder=ROOT/'data/site'
rows=json.loads((ROOT/'data/research/video_meta.json').read_text(encoding='utf-8'))
MONTHS={'янв':1,'февр':2,'мар':3,'апр':4,'мая':5,'июн':6,'июл':7,'авг':8,'сент':9,'окт':10,'нояб':11,'дек':12}
def text(value):return value.get('simpleText') or ''.join(r.get('text','') for r in value.get('runs',[]))
def date(value):
    if re.match(r'\d{4}-\d{2}-\d{2}',value):return value[:10]
    m=re.search(r'(\d+)\s+([а-я]+)\.?\s+(\d{4})',value)
    return f'{m[3]}-{MONTHS[m[2]]:02}-{int(m[1]):02}' if m and m[2] in MONTHS else ''
videos=[];channels={};rejected=[]
for row in rows:
    header=next((r['videoDescriptionHeaderRenderer'] for r in row.get('public_metadata',[]) if 'videoDescriptionHeaderRenderer' in r),{})
    owner=next((r['videoOwnerRenderer'] for r in row.get('public_metadata',[]) if 'videoOwnerRenderer' in r),{})
    published=date(row.get('micro',{}).get('publishDate','') or text(header.get('publishDate',{})))
    if not published:rejected.append({'url':row['url'],'reason':'Не удалось подтвердить дату публикации'});continue
    name=text(header.get('channel',{})) or row.get('channel','')
    endpoint=owner.get('navigationEndpoint',{})
    channel_path=endpoint.get('commandMetadata',{}).get('webCommandMetadata',{}).get('url','')
    channel_id=endpoint.get('browseEndpoint',{}).get('browseId') or row.get('details',{}).get('channelId')
    channel_url='https://www.youtube.com'+channel_path if channel_path.startswith('/') else ('https://www.youtube.com/channel/'+channel_id if channel_id else '')
    title=text(header.get('title',{})) or row['title']
    current=row['id'] in ('2i8uS3pn464','61gIB6cYPFM') and published.startswith('2026')
    if not current and not published.startswith('2025'):rejected.append({'url':row['url'],'reason':'Связь с MITEX 2026 не подтверждена'});continue
    kind='invitation' if current else 'archive'
    videos.append({'id':row['id'],'title':title,'url':row['url'],'date':published,'duration':row.get('duration',0),'channel':name,'channel_url':channel_url,
        'thumb':'https://i.ytimg.com/vi/'+row['id']+'/hqdefault.jpg','kind':kind,'exhibition_year':2026 if current else 2025,
        'description':'Приглашение и подготовка к выставке 2026 года.' if current else 'Публикация ноября 2025 года. Указание 2026 в заголовке относится к сезону/моделям; это архивный материал.',
        'status':'Приглашение на MITEX 2026' if current else 'Архив 2025', 'verification':'Дата и автор проверены по публичным метаданным YouTube.'})
    if channel_url:channels[channel_url]={'name':name,'url':channel_url,'source_url':row['url'],'kind':'video_author'}
records=json.loads((folder/'exhibitors.json').read_text(encoding='utf-8'))
for x in records:
    for s in x['contacts']['social']:
        if s['network']=='YouTube' and any(part in s['url'] for part in ('/@','/channel/','/user/','/c/')):
            url=s['url'].split('?')[0].rstrip('/')
            channels.setdefault(url,{'name':x['name'],'url':url,'source_url':s['source_url'],'kind':'exhibitor_channel','exhibitor_id':x['id']})
videos.sort(key=lambda v:v['date'],reverse=True)
(folder/'videos.json').write_text(json.dumps(videos,ensure_ascii=False,indent=2),encoding='utf-8')
(folder/'video_channels.json').write_text(json.dumps(list(channels.values()),ensure_ascii=False,indent=2),encoding='utf-8')
(folder/'video_report.json').write_text(json.dumps({'verified_2026':sum(v['kind']=='invitation' for v in videos),'archive':sum(v['kind']=='archive' for v in videos),'channels':len(channels),'excluded':rejected},ensure_ascii=False,indent=2),encoding='utf-8')
print('VIDEOS',len(videos),'CHANNELS',len(channels),'2026',sum(v['kind']=='invitation' for v in videos))
