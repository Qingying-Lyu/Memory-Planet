const {test}=require('node:test');const assert=require('node:assert/strict');const {videoId,parseDirectory}=require('./bilibili.cjs');
test('同课程不同分P定位同一个BV',()=>{assert.equal(videoId('https://www.bilibili.com/video/BV1owrpYKEtP/?p=2&vd_source=test'),'BV1owrpYKEtP')});
test('仅接受B站视频地址，不访问任意主机',()=>{for(const u of ['https://example.com/video/BV1owrpYKEtP/','http://127.0.0.1/','https://www.bilibili.com.evil.test/video/BV1owrpYKEtP/'])assert.throws(()=>videoId(u))});
test('多P保留标题和顺序并去掉追踪参数',()=>{const r=parseDirectory({code:0,data:{title:'课程',bvid:'BV1owrpYKEtP',pages:[{page:1,part:'第一节'},{page:2,part:'第二节'}]}});assert.equal(r.chapters.length,2);assert.equal(r.chapters[1].url,'https://www.bilibili.com/video/BV1owrpYKEtP/?p=2');assert.equal(r.chapters[1].title,'第二节')});
test('合集独立BV保持分组且去重',()=>{const ep={bvid:'BV1owrpYKEtP',title:'一节'};const r=parseDirectory({code:0,data:{ugc_season:{title:'合集',sections:[{title:'第一章',episodes:[ep,ep]}]}}});assert.equal(r.chapters.length,1);assert.equal(r.chapters[0].section,'第一章')});
test('平台失败与空目录不冒充识别成功',()=>{assert.throws(()=>parseDirectory({code:-403}));assert.throws(()=>parseDirectory({code:0,data:{}}))});
