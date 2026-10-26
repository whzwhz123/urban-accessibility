"""Browser acceptance against local servers; external AMap is blocked."""
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/workflow_validation'/('browser_acceptance_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
OUT.mkdir(parents=True,exist_ok=False)
def api(path):return json.load(urlopen('http://127.0.0.1:8765'+path))
errors=[];failures=[];checks=[]
def record(name):checks.append(name);print('PASS',name,flush=True)
def main():
    cities=api('/api/cities')['cities'];city=next(row for row in cities if row['id']=='Linyi')
    demand=next(row for row in api('/api/cities/Linyi/demand-runs')['runs'] if row.get('current_inputs'))
    runs=[row for row in api('/api/cities/Linyi/runs')['runs'] if row.get('demand_run_id')==demand['run_id']]
    evaluation=next(row for row in api('/api/cities/Linyi/composite-runs')['runs'] if row.get('demand_run_id')==demand['run_id'])
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True,args=['--enable-unsafe-swiftshader'])
        context=browser.new_context(viewport={'width':1440,'height':1000},accept_downloads=True)
        context.route(re.compile(r'https?://[^/]*(amap|autonavi)\.'),lambda route:route.abort())
        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('response',lambda response:failures.append({'url':response.url,'status':response.status}) if '/api/' in response.url and response.status>=400 else None)
        page.goto('http://127.0.0.1:3000/',wait_until='networkidle')
        page.get_by_text('北京市 ·',exact=False).first.wait_for();record('default_beijing_startup')
        page.screenshot(path=str(OUT/'01_beijing.png'))
        state={'schema':1,'city_id':'Linyi','input_version':city.get('custom_input_version'),'demand_id':demand['run_id'],'assignment_id':runs[0]['run_id'],'evaluation_id':evaluation['run_id'],'active_tab':'assignment','vis':{'demand':False,'isochrone':False,'coverage':False,'tci':False,'lines':True,'stops':True,'composite':True,'imbalance':False},'tci_session':'midday'}
        page.evaluate('(state)=>sessionStorage.setItem("urban-workspace-session-v1",JSON.stringify(state))',state)
        page.reload(wait_until='networkidle');page.get_by_text(re.compile('条物理路段')).first.wait_for(timeout=30000)
        assert page.locator('canvas').count()>0;record('historical_linyi_flow_restore')
        page.get_by_role('button',name='收起操作面板',exact=True).click();page.screenshot(path=str(OUT/'02_linyi_flow.png'))
        page.get_by_role('button',name='V/C',exact=True).click();page.screenshot(path=str(OUT/'03_linyi_vc.png'));record('flow_vc_switch')
        with page.expect_download() as download:
            page.get_by_role('button',name='导出地图 PNG',exact=True).click()
        download.value.save_as(str(OUT/'map_export.png'));assert (OUT/'map_export.png').stat().st_size>2000;record('png_download')
        with page.expect_popup() as popup:
            page.get_by_role('button',name='地图打印 / PDF',exact=True).click()
        popup.value.wait_for_load_state();assert popup.value.locator('img').count()==1
        assert popup.value.locator('img').evaluate('(img)=>img.complete && img.naturalWidth>0');popup.value.close();record('print_pdf_layout')
        page.get_by_role('button',name='打开操作面板',exact=True).click()
        page.get_by_role('combobox',name='对比实验',exact=True).click()
        page.locator('.ant-select-dropdown .ant-select-item-option').filter(has_text=runs[1]['run_id']).click()
        page.get_by_role('button',name='联动地图 / 路段差值对照').click()
        page.get_by_role('heading',name='分配结果地图对比').wait_for()
        page.get_by_text(re.compile('匹配 .*段；左侧未匹配')).first.wait_for(timeout=30000)
        page.get_by_role('button',name='收起操作面板',exact=True).click()
        page.screenshot(path=str(OUT/'04_comparison.png'))
        page.get_by_role('button',name='切换差值图（右 - 左）').click();page.screenshot(path=str(OUT/'05_difference.png'));record('comparison_and_difference')
        page.get_by_role('button',name='关闭',exact=True).click()
        page.get_by_role('button',name='打开操作面板',exact=True).click()
        page.get_by_role('tab',name='综合评价',exact=True).click()
        page.get_by_role('button',name='收起操作面板',exact=True).click();page.wait_for_timeout(1000)
        page.screenshot(path=str(OUT/'06_composite.png'));record('composite_layer')
        page.mouse.move(1000,500);page.mouse.down();page.mouse.move(1100,550,steps=8);page.mouse.up();record('map_pan')
        page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(OUT/'07_mobile.png'))
        page.get_by_role('button',name='打开操作面板',exact=True).click();page.get_by_role('tab',name='数据管理',exact=True).click();record('mobile_panel_navigation')
        assert not errors,errors
        assert not failures,failures
        (OUT/'summary.json').write_text(json.dumps({'status':'passed','checks':checks,'javascript_errors':errors,'api_failures':failures,'city_id':'Linyi','run_id':runs[0]['run_id'],'scope':'UI only; existing model outputs, not prediction-accuracy validation'},ensure_ascii=False,indent=2),encoding='utf-8')
        print('BROWSER_OUTPUT',OUT,flush=True);browser.close()

if __name__=='__main__':
    try:main()
    except Exception as error:
        (OUT/'failure.json').write_text(json.dumps({'error':str(error),'checks':checks,'javascript_errors':errors,'api_failures':failures},ensure_ascii=False,indent=2),encoding='utf-8');raise
