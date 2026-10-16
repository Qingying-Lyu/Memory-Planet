import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from . import search_service as s,enrichment
class SearchTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
  self.patches=[patch.object(s,'CACHE',root/'cache'),patch.object(s,'HEALTH',root/'health.json'),patch.object(s,'load',return_value={})]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def test_positive_cache_avoids_network(self):
  raw=b'<rss><channel><item><link>https://docs.example.org/cache</link></item></channel></rss>'
  with patch.object(enrichment,'fetch',return_value=('https://www.bing.com',raw)) as fetch:
   self.assertEqual(s.search('cache',fetch),['https://docs.example.org/cache'])
   self.assertEqual(s.search('cache',fetch),['https://docs.example.org/cache'])
   self.assertEqual(fetch.call_count,1)
 def test_provider_failures_open_circuit_and_negative_cache(self):
  with patch.object(enrichment,'fetch',side_effect=OSError('DNS')) as fetch:
   self.assertEqual(s.search('one',fetch),[])
   count=fetch.call_count
   self.assertEqual(s.search('two',fetch),[])
   self.assertEqual(fetch.call_count,count)
   self.assertEqual(s.health()['bing']['state'],'dns_failed')
 def test_configured_api_is_first_choice(self):
  with patch.object(s,'load',return_value={'search_provider':'tavily','search_key_file':'secret.txt'}),patch.object(s,'api_search',return_value=['https://docs.example.org/cache']) as api,patch.object(enrichment,'fetch') as fetch:
   self.assertEqual(s.search('cache',fetch),['https://docs.example.org/cache'])
   api.assert_called_once_with('tavily','cache','secret.txt');fetch.assert_not_called()
 def test_article_cache_survives_connection_failure(self):
  with patch.object(enrichment,'_fetch',return_value=('https://docs.example.org/page',b'public body')) as fetch:
   self.assertEqual(enrichment.fetch('https://docs.example.org/page')[1],b'public body')
   fetch.side_effect=AssertionError('cache should avoid fetch')
   self.assertEqual(enrichment.fetch('https://docs.example.org/page')[1],b'public body')

 def test_starflow_requires_real_search_usage(self):
  with patch.object(s,'read_secret_file',return_value='test-only'),patch.object(s.httpx,'Client') as client:
   response=client.return_value.__enter__.return_value.post.return_value
   response.json.return_value={'usage':{'web_search_queries':0},'choices':[{'message':{'content':'{"sources":[{"url":"https://example.org/guessed"}]}'}}]}
   with self.assertRaises(ValueError):s.starflow_search('tf2','glm-5.3-flash','key-path')
   response.json.return_value['usage']['web_search_queries']=1
   self.assertEqual(s.starflow_search('tf2','glm-5.3-flash','key-path'),['https://example.org/guessed'])
   request=client.return_value.__enter__.return_value.post.call_args
   self.assertEqual(request.args[0],'https://kspmas.ksyun.com/v1/chat/completions')
   self.assertEqual(request.kwargs['json']['tools'],[{'type':'web_search'}])
 def test_starflow_falls_back_to_second_model(self):
  config={'search_provider':'starflow','starflow_key_file':'key-path','search_models':['primary','backup']}
  with patch.object(s,'load',return_value=config),patch.object(s,'starflow_search',side_effect=[ValueError('no search'),['https://docs.example.org/page']]) as api,patch.object(enrichment,'fetch') as fetch:
   self.assertEqual(s.search('tf2',fetch),['https://docs.example.org/page'])
   self.assertEqual([c.args[1] for c in api.call_args_list],['primary','backup']);fetch.assert_not_called()

 def test_starflow_detects_truncated_output(self):
  with patch.object(s,'read_secret_file',return_value='test-only'),patch.object(s.httpx,'Client') as client:
   response=client.return_value.__enter__.return_value.post.return_value
   response.json.return_value={'usage':{'web_search_queries':1},'choices':[{'finish_reason':'length','message':{'content':''}}]}
   with self.assertRaisesRegex(ValueError,'截断'):s.starflow_search('tf2','glm-5.3-flash','key-path')
   self.assertEqual(client.return_value.__enter__.return_value.post.call_args.kwargs['json']['max_tokens'],8000)
