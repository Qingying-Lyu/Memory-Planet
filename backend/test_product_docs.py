import json,tempfile,unittest
from pathlib import Path
from .product_docs import directory,article,collect,supports,expand_directory,EmptyVideoSource,api_reference,learning_text
ROOT='https://docs.ksyun.com/products/218'
class ProductDocsTests(unittest.TestCase):
 def test_only_product_directory_links_deduplicated(self):
  raw='<a href="/documents/99">其他产品</a><div class="ks-doc-left-public-nav"><div class="list-sec"><a href="/documents/1?type=3">概念</a><a href="/documents/1?type=3">重复</a><a href="https://evil.example/documents/2">站外</a><a href="/products/2">其他产品</a></div></div>'
  rows=directory(raw,ROOT);self.assertEqual(len(rows),1);self.assertTrue(rows[0]['url'].endswith('/documents/1?type=3'))
 def test_article_keeps_table_code_and_image_excludes_navigation(self):
  raw='<nav>站点菜单</nav><div class="ks-docs-editor__content"><h2>标题</h2><table><tr><th>参数</th><th>值</th></tr><tr><td>abc</td><td>123</td></tr></table><pre>docker pull image</pre><img src="/x.png" alt="架构"></div>'
  text=article(raw,ROOT);self.assertIn('| 参数 | 值 |',text);self.assertIn('docker pull image',text);self.assertIn('https://docs.ksyun.com/x.png',text);self.assertNotIn('站点菜单',text)
 def test_failed_page_not_silently_accepted_and_retry_resumes(self):
  with tempfile.TemporaryDirectory() as tmp:
   calls=[];fail=[True]
   def fetch(url):
    calls.append(url)
    if url==ROOT:return url,b'<div class="ks-doc-left-public-nav"><div class="list-sec"><a href="/documents/1">one</a><a href="/documents/2">two</a></div></div>'
    if url.endswith('/2') and fail[0]:raise TimeoutError()
    return url,b'<div class="ks-docs-editor__content">valid page content</div>'
   with self.assertRaises(ValueError):collect(ROOT,tmp,fetch)
   fail[0]=False;blocks=collect(ROOT,tmp,fetch)
   self.assertEqual(calls.count('https://docs.ksyun.com/documents/1'),1)
   self.assertEqual(len(blocks),2);self.assertIn('来源：',blocks[0][2])
   self.assertEqual(json.loads((Path(tmp)/'product-manifest.json').read_text())['completed'],2)
 def test_multiple_category_hops_reach_final_article(self):
  def nav(inner):return '<div class="ks-doc-left-public-nav"><a href="/products/218">产品</a><div class="list-sec">'+inner+'</div></div>'
  pages={'https://docs.ksyun.com/directories/1':nav('<li><a href="/directories/2">产品概述</a></li>'),'https://docs.ksyun.com/directories/2':nav('<li><a href="/documents/7">容器服务正文</a></li>')}
  result=expand_directory(nav('<li><a href="/directories/1">产品简介</a></li>'),ROOT,lambda u:(u,pages[u]))
  self.assertEqual([e['url'] for e in result],['https://docs.ksyun.com/documents/7'])
 def test_navigation_inside_content_container_rejected(self):
  with self.assertRaises(ValueError):article('<div class="ks-doc-main">全部文档 当前文档 热搜词推荐 云服务器</div>',ROOT)
 def test_inline_nested_article_does_not_need_category_fetch(self):
  raw='<div class="ks-doc-left-public-nav"><div class="list-sec"><li><a href="/directories/1">目录</a><ul><li><a href="/documents/7">正文</a></li></ul></li></div></div>'
  self.assertEqual(len(expand_directory(raw,ROOT,lambda u:self.fail('不应重复请求已展开目录'))),1)
 def test_empty_editor_falls_back_to_actual_main_content(self):
  text=article('<div class="ks-doc-main"><div class="ks-docs-editor__content"></div><p>真正的产品正文内容</p></div>',ROOT)
  self.assertIn('真正的产品正文内容',text)
 def test_official_empty_video_source_is_not_fabricated_as_knowledge(self):
  with self.assertRaises(EmptyVideoSource):article('<div class="ks-doc-main"><div class="video-doc-container"><video src="//"></video></div><div class="ks-docs-editor__content"></div></div>',ROOT)
 def test_api_reference_removed_by_type_or_directory_scope(self):
  self.assertTrue(api_reference({'url':'https://docs.ksyun.com/documents/1?type=6'}))
  self.assertTrue(api_reference({'url':'https://docs.ksyun.com/documents/1?type=3','path':['API目录','接口说明']}))
  self.assertFalse(api_reference({'url':'https://docs.ksyun.com/documents/1?type=3','path':['用户指南','使用API网关']}))
 def test_learning_filter_retains_short_commands_constraints_and_source(self):
  url='https://docs.ksyun.com/documents/1'
  text='限制：仅支持指定网络。\n\n```\ndocker pull nginx\n```\n\n```\n'+('config: value\n'*30)+'```\n\n操作后检查运行状态。'
  cleaned,stats=learning_text(text,url)
  self.assertIn('docker pull nginx',cleaned);self.assertIn('限制：仅支持指定网络',cleaned);self.assertIn('操作后检查运行状态',cleaned)
  self.assertNotIn('config: value',cleaned);self.assertIn(url,cleaned);self.assertEqual(stats['large_code_blocks_removed'],1)
 def test_wrong_root_rejected(self):
  self.assertFalse(supports('https://evil.example/products/218'));self.assertFalse(supports('https://docs.ksyun.com/documents/1'))
  with self.assertRaises(ValueError):directory('<html>导航缺失</html>',ROOT)
if __name__=='__main__':unittest.main()
