import base64, hashlib, json
import httpx, pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from drc_sync import updater as u

@pytest.fixture
def signed(monkeypatch):
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    original=u.verify_manifest
    monkeypatch.setattr(u,'verify_manifest',lambda x:original(x,public))
    def sign(**overrides):
        r={'schema':1,'app':'chintamani-gems','platform':'windows-x64','version':'0.7.0','url':'https://store.public.blob.vercel-storage.com/releases/setup.exe','size':3,'sha256':hashlib.sha256(b'abc').hexdigest(),'notes':'Test release'}
        r.update(overrides);body=json.dumps(r).encode()
        return {'payload':base64.b64encode(body).decode(),'signature':base64.b64encode(key.sign(body)).decode()}
    return sign


def test_signed_feed_newer_only(signed):
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=signed())) ) as c:
        assert u.latest(c,'0.6.0')['version']=='0.7.0'
        assert u.latest(c,'0.7.0') is None
        assert u.latest(c,'0.8.0') is None

@pytest.mark.parametrize('status',[204,404])
def test_feed_not_published(status):
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(status))) as c:
        assert u.latest(c) is None


def test_tampered_manifest_rejected(signed):
    envelope=signed();envelope['payload']=base64.b64encode(b'{}').decode()
    with pytest.raises(u.UpdateError):u.verify_manifest(envelope)

@pytest.mark.parametrize('changes',[{'platform':'linux'},{'app':'other'},{'version':'0.7.0-beta'},{'size':True},{'size':u.MAX_INSTALLER+1},{'sha256':'nope'},{'url':'http://store.public.blob.vercel-storage.com/a.exe'},{'url':'https://public.blob.vercel-storage.com.evil.com/a'},{'url':'https://user@store.public.blob.vercel-storage.com/a'}])
def test_invalid_release_rejected(signed,changes):
    with pytest.raises(u.UpdateError):u.verify_manifest(signed(**changes))


def test_download_cache_and_integrity(signed,tmp_path):
    release=u.verify_manifest(signed());calls=[]
    def respond(req):calls.append(req);return httpx.Response(200,content=b'abc')
    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        path=u.download(c,release,tmp_path)
        assert path.read_bytes()==b'abc'
        assert u.download(c,release,tmp_path)==path
        assert len(calls)==1
        path.write_bytes(b'bad')
        assert u.download(c,release,tmp_path).read_bytes()==b'abc'
        assert len(calls)==2

@pytest.mark.parametrize('body',[b'a',b'abcd',b'bad'])
def test_bad_download_never_installed(signed,tmp_path,body):
    release=u.verify_manifest(signed())
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=body))) as c:
        with pytest.raises(u.UpdateError):u.download(c,release,tmp_path)
    assert not list(tmp_path.iterdir())


def test_no_redirects_or_http_failure(signed,tmp_path):
    release=u.verify_manifest(signed())
    for status in (302,500):
        with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(status,headers={'Location':'https://evil.com'}))) as c:
            with pytest.raises(httpx.HTTPStatusError):u.download(c,release,tmp_path)


def test_feed_capped():
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=b'x'*(u.MAX_FEED+1)))) as c:
        with pytest.raises(u.UpdateError):u.latest(c)


def test_handoff_escapes_paths_and_waits_for_exit(signed,tmp_path):
    r=u.verify_manifest(signed());script=u.handoff_script(tmp_path/"owner's setup.exe",r,tmp_path/"owner's app",123)
    assert 'WaitForExit(120000)' in script
    assert "owner''s setup.exe" in script and "owner''s app" in script
    assert '/NOCLOSEAPPLICATIONS' in script and '/UPDATE=1' in script
    assert 'ComputeHash' in script and 'Get-FileHash' not in script


def test_install_refuses_source_and_modified_binary(signed,tmp_path,monkeypatch):
    r=u.verify_manifest(signed());p=tmp_path/'installer.exe';p.write_bytes(b'bad')
    monkeypatch.setattr(u,'installation_dir',lambda:tmp_path)
    monkeypatch.setattr(u.subprocess,'Popen',lambda *a,**k:pytest.fail('must not launch'))
    with pytest.raises(u.UpdateError):u.launch_installer(p,r)

def test_update_restart_waits_for_inventory_worker(tmp_path,monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication, QMessageBox
    from drc_sync.app import Window,Worker
    from drc_sync.update_ui import UpdateController
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker,'start',lambda self:None)
    window=Window(tmp_path/'ui.db');controller=UpdateController(window,tmp_path/'updates')
    controller.timer.stop()
    controller.ready=({'version':'0.7.0'},tmp_path/'setup.exe')
    monkeypatch.setattr(QMessageBox,'exec',lambda self:QMessageBox.Yes)
    monkeypatch.setattr(window.worker,'isRunning',lambda:True)
    requested=[];launched=[]
    monkeypatch.setattr(window.worker,'requestInterruption',lambda:requested.append(True))
    monkeypatch.setattr('drc_sync.update_ui.launch_installer',lambda *args:launched.append(args))
    monkeypatch.setattr(window,'exit',lambda:None)
    controller.offer_install()
    assert requested and not launched
    controller.worker_stopped()
    assert len(launched)==1
    window.tray.hide();window.timer.stop();window.store.db.close();window.deleteLater();app.processEvents()

def test_network_failure_keeps_current_app(signed,tmp_path):
    def offline(req):raise httpx.ConnectError('private proxy credentials',request=req)
    with httpx.Client(transport=httpx.MockTransport(offline)) as c:
        with pytest.raises(httpx.ConnectError):u.latest(c)
        with pytest.raises(httpx.ConnectError):u.download(c,u.verify_manifest(signed()),tmp_path)
    assert not list(tmp_path.iterdir())


def test_update_worker_redacts_failures(tmp_path,monkeypatch):
    from drc_sync.update_ui import UpdateTask
    def unavailable():raise RuntimeError('APIKEY=secret')
    monkeypatch.setattr('drc_sync.update_ui.update_client',unavailable)
    task=UpdateTask(tmp_path);errors=[];task.failed.connect(errors.append);task.run()
    assert errors==['RuntimeError']

@pytest.mark.parametrize('folder,expected',[('DRCInventorySync','ChintamaniGems'),('ChintamaniGems','ChintamaniGems'),('CustomFolder','CustomFolder')])
def test_installation_folder_branding(tmp_path,monkeypatch,folder,expected):
    monkeypatch.setattr(u.sys,'frozen',True,raising=False)
    monkeypatch.setattr(u.sys,'executable',str(tmp_path/folder/'ChintamaniGems.exe'))
    assert u.installation_dir()==tmp_path/expected


def test_manual_click_during_automatic_check_shows_result(tmp_path,monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from types import SimpleNamespace
    from PySide6.QtCore import QObject,Signal
    from PySide6.QtWidgets import QApplication,QWidget,QToolButton,QMessageBox
    from drc_sync.update_ui import UpdateController
    app=QApplication.instance() or QApplication([])
    class Worker(QObject):
        finished=Signal()
    window=QWidget();window.update_button=QToolButton(window);window.worker=Worker(window)
    controller=UpdateController(window,tmp_path);controller.timer.stop()
    controller.task=SimpleNamespace(isRunning=lambda:True)
    messages=[]
    monkeypatch.setattr(QMessageBox,'information',lambda *args:messages.append(args[2]))
    window.update_button.click()
    assert controller.manual and 'Checking' in window.update_button.text()
    controller.received(None,None)
    assert len(messages)==1 and 'latest published version' in messages[0]
    controller.task=None;window.deleteLater();app.processEvents()


def test_source_check_reports_available_without_installing(tmp_path,monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtCore import QObject,Signal
    from PySide6.QtWidgets import QApplication,QWidget,QToolButton,QMessageBox
    from drc_sync.update_ui import UpdateController
    app=QApplication.instance() or QApplication([])
    class Worker(QObject):
        finished=Signal()
    window=QWidget();window.update_button=QToolButton(window);window.worker=Worker(window)
    controller=UpdateController(window,tmp_path,can_install=False);controller.timer.stop();controller.manual=True
    messages=[]
    monkeypatch.setattr(QMessageBox,'information',lambda *args:messages.append(args[2]))
    controller.received({'version':'99.0.0'},None)
    assert not controller.ready and not controller.installing
    assert len(messages)==1 and 'Python source' in messages[0] and 'api/download' in messages[0]
    window.deleteLater();app.processEvents()


def test_source_task_does_not_download_installer(tmp_path,monkeypatch):
    import drc_sync.update_ui as ui
    from contextlib import nullcontext
    monkeypatch.setattr(ui,'update_client',lambda:nullcontext(object()))
    monkeypatch.setattr(ui,'latest',lambda client:{'version':'99.0.0'})
    monkeypatch.setattr(ui,'download',lambda *args:pytest.fail('Source checks must not download an installer'))
    task=ui.UpdateTask(tmp_path,download_installer=False);results=[]
    task.result.connect(lambda release,path:results.append((release,path)));task.run()
    assert results==[({'version':'99.0.0'},None)]
