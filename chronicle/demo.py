"""Generated demo workspace. Never opens WeChat or a real credential store."""
from datetime import datetime,timedelta
import hashlib
from pathlib import Path
import shutil
import wave
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

from .archive import Archive,identity
from .desktop_storage import DesktopStore
from .launch import BrowserWorkbench,UnavailableVault


def demo_message(number,chat,person,timestamp,text,kind=1):
    return {'archive_identity':identity('d'*64,chat,str(number)),'conversation_id':chat,
        'sender_id':person,'source_database':'demo.db','server_id':str(number),'local_id':number,
        'timestamp':timestamp,'sort_sequence':number,'type_code':kind,'body':text,'decode_status':'decoded'}


class DemoSource:
    account='d'*64
    def __init__(self,root,messages):self.root=root;self.items=messages
    def conversations(self):return [
        {'conversation_id':'demo-advisor','display_name':'林老师（示例）','kind':'private','nickname':'林老师','remark':''},
        {'conversation_id':'demo-group','display_name':'课题讨论组（示例）','kind':'group','nickname':'课题讨论组','remark':''}]
    def message_page(self,conversation,limit=50,before=None):
        rows=[m for m in self.items if m['conversation_id']==conversation]
        return {'messages':rows,'has_more':False,'before':None}


class DemoWorkbench(BrowserWorkbench):
    is_demo=True
    def __init__(self,home):
        store=DesktopStore(home)
        store.config['welcome_complete']=True;store.save()
        super().__init__(store,vault=UnavailableVault())
        self.demo_assets=store.home/'generated-assets';self.demo_assets.mkdir(exist_ok=True,mode=0o700)
        day=datetime.now(ZoneInfo('Asia/Shanghai')).replace(hour=10,minute=0,second=0,microsecond=0)
        yesterday=day-timedelta(days=1)
        base=int(yesterday.timestamp());today=int(day.timestamp())
        quote=ET.Element('msg');app=ET.SubElement(quote,'appmsg')
        ET.SubElement(app,'type').text='57';ET.SubElement(app,'title').text='章节顺序就按这个版本推进，先把数据说明补齐。'
        ref=ET.SubElement(app,'refermsg');ET.SubElement(ref,'type').text='49';ET.SubElement(ref,'displayname').text='我'
        ET.SubElement(ref,'content').text='<msg><appmsg><type>6</type><title>研究提纲.txt</title></appmsg></msg>'
        self.demo_messages=[
            demo_message(1,'demo-advisor','demo-me',base,'老师，这是这一周的研究提纲，想先确认一下章节顺序。'),
            demo_message(2,'demo-advisor','demo-me',base+120,'<msg><appmsg><type>6</type><title>研究提纲.txt</title></appmsg></msg>',49),
            demo_message(3,'demo-advisor','demo-teacher',base+300,'整体方向清楚了。方法部分先说明为什么选择这组数据，再展开评价指标。'),
            demo_message(4,'demo-advisor','demo-me',base+420,'收到，我把需要确认的两点整理到组里。'),
            demo_message(5,'demo-group','demo-me',base+600,'同步一下今天的讨论：本周补齐数据说明，下周集中看方法章节。'),
            demo_message(6,'demo-group','demo-member',base+720,'我来整理数据来源，周五之前发到这里。'),
            demo_message(7,'demo-advisor','demo-teacher',today,ET.tostring(quote,encoding='unicode'),(57<<32)+49),
            demo_message(8,'demo-group','demo-me',today+180,'<msg><voicemsg voicelength="600"/></msg>',34),
            demo_message(9,'demo-group','demo-teacher',today+240,'下次讨论前，再对照提纲检查一次。修改后的材料也可以归档到同一个项目。')]
        self.demo_source=DemoSource(store.home/'synthetic/db_storage',self.demo_messages)
        def asset(data,extension):
            name=hashlib.sha256(data).hexdigest()+extension
            (self.demo_assets/name).write_bytes(data)
            return name
        file=asset('示例研究提纲\n\n1. 问题与目标\n2. 数据来源\n3. 方法与评价\n4. 结果与讨论\n'.encode(),'.txt')
        voice_path=store.home/'voice.wav'
        with wave.open(str(voice_path),'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(24000);audio.writeframes(b'\0\0'*14400)
        voice=asset(voice_path.read_bytes(),'.wav');voice_path.unlink()
        self.demo_resources={self.demo_messages[1]['archive_identity']:{'archive_identity':self.demo_messages[1]['archive_identity'],'status':'copied','local_asset':file,'name':'研究提纲.txt','bytes':(self.demo_assets/file).stat().st_size},
            self.demo_messages[7]['archive_identity']:{'archive_identity':self.demo_messages[7]['archive_identity'],'status':'copied','local_asset':voice,'voice_playback_asset':voice,'duration_seconds':.6}}
        with Archive(self.archive_root) as archive:
            if not archive.projects():
                project=archive.create_project('论文协作 · 示例')
                archive.import_messages(project,{'account_fingerprint':'d'*64,'messages':self.demo_messages},list(self.demo_resources.values()),self.demo_assets,
                    names={r['conversation_id']:r['display_name'] for r in self.demo_source.conversations()})
                archive.set_people('d'*64,self.people())
                archive.correct_transcript(self.demo_messages[7]['archive_identity'],'示例人工文字：本周先补齐数据说明，周五一起核对。')
    def source(self):return self.demo_source
    def people(self,source=None):return {'demo-me':'我','demo-teacher':'林老师','demo-member':'周同学'}
    def status(self):return {'source_available':True,'conversation_count':2,'setup_available':True,'desktop':False,'mode':'source','demo':True}
    def desktop_state(self):return dict(super().desktop_state(),demo=True)
    def known_connections(self):return []
    def preflight(self):return {'accounts':[],'developer_tools_ready':False,'wechat_running':False,'demo':True}
    def begin_connection(self,*args,**kwargs):raise ValueError('demo_read_only_setup')
    def prepare(self,token,message_id):
        session=self.session(token)
        if message_id not in session['messages']:raise ValueError('message_not_in_selection')
        record=self.demo_resources.get(message_id)
        if record:
            directory=session['directory']/'assets';directory.mkdir(exist_ok=True,mode=0o700)
            for field in ('local_asset','voice_playback_asset'):
                if record.get(field):shutil.copyfile(self.demo_assets/record[field],directory/record[field])
        return record
