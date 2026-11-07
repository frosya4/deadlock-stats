import re, os, json, glob
G=r"C:/Program Files/steam/steamapps/common/Deadlock/game/citadel"
VD=os.path.join(os.environ['TEMP'],'vdata','scripts')
def murmur2(key:bytes, seed=0x31415926):
    m=0x5bd1e995; r=24; ln=len(key); h=(seed ^ ln)&0xffffffff; i=0
    while ln>=4:
        k=int.from_bytes(key[i:i+4],'little'); k=(k*m)&0xffffffff; k^=k>>r; k=(k*m)&0xffffffff
        h=(h*m)&0xffffffff; h^=k; i+=4; ln-=4
    if ln==3: h^=key[i+2]<<16
    if ln>=2: h^=key[i+1]<<8
    if ln>=1: h^=key[i]; h=(h*m)&0xffffffff
    h^=h>>13; h=(h*m)&0xffffffff; h^=h>>15
    return h
def blocks(path):
    s=open(path,encoding='utf-8').read().replace('\r','')
    parts=re.split(r'^\t([A-Za-z_]\w*) = \n\t\{',s,flags=re.M)
    out={}
    for i in range(1,len(parts),2):
        body=parts[i+1].split('\n\t}',1)[0]
        f={k:v.strip().strip('"') for k,v in re.findall(r'^\t\t(\w+) = (.+)$',body,re.M)}
        out[parts[i]]=f
    return out
def loc(lang):
    d={}
    for fn in glob.glob(G+f'/resource/localization/*/*_{lang}.txt'):
        for k,v in re.findall(r'^\s*"([^"]+)"\s+"((?:[^"\\]|\\.)*)"',open(fn,encoding='utf-8-sig',errors='replace').read(),re.M):
            d.setdefault(k,re.sub(r'#\|\w\|#','',v))
    return d
def build():
    ru,en=loc('russian'),loc('english')
    heroes={}
    for k,f in blocks(VD+'/heroes.vdata').items():
        if 'm_HeroID' in f:
            hid=int(f['m_HeroID']); heroes[hid]={'code':k,'ru':ru.get(k+':n') or ru.get(k) or k,'en':en.get(k+':n') or en.get(k) or k}
    items={}
    for k,f in blocks(VD+'/abilities.vdata').items():
        items[murmur2(k.encode())]={'code':k,'ru':ru.get(k) or en.get(k) or k,'en':en.get(k) or k,'class':f.get('_class'),
          'slot':f.get('m_eItemSlotType'),'tier':f.get('m_iItemTier'),'atype':f.get('m_eAbilityType')}
    return heroes,items
if __name__=='__main__':
    h,i=build(); json.dump({'heroes':h,'items':{str(k):v for k,v in i.items()}},open(os.path.join(os.environ['TEMP'],'an','refs.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=0)
    print(len(h),len(i)); print([v for k,v in h.items()][:3]); 
    for x in (715762406,2824119765,3862866912,4072270083,2951612397): print(x,i.get(x))
