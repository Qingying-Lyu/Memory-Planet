import unittest,tempfile
from pathlib import Path
from .wps_reader import document_type,diagram_blocks
from .office_reader import spreadsheet_blocks

class CloudReaderTests(unittest.TestCase):
 def test_actual_word_type_wins_over_media_path(self):
  self.assertEqual(document_type({'title':'培训.docx'},'https://plus.wps.cn/view/media/l/example'),'word')
 def test_actual_sheet_type_wins_over_ppt_hint(self):
  self.assertEqual(document_type({'title':'数据.xlsx'},'https://365.kdocs.cn/l/example?officeType=p'),'spreadsheet')
 def test_mindmap_preserves_collapsed_children_and_images(self):
  d={'id':'root','title':'K8s','children':[{'id':'a','title':'Pod','collapsed':True,'children':[{'id':'b','title':'初始化容器','image':{'url':'https://tc-cdn.processon.com/wps/img'}}]}]}
  rows,images,kind=diagram_blocks(d)
  self.assertEqual(rows[-1]['text'],'K8s > Pod > 初始化容器');self.assertEqual(kind,'mindmap');self.assertEqual(len(images),1)
 def test_flowchart_preserves_branches(self):
  d={'elements':{'a':{'textBlock':[{'text':'库存检查'}]},'b':{'textBlock':[{'text':'交付'}]},'e':{'name':'linker','from':{'id':'a'},'to':{'id':'b'},'text':'有库存'}}}
  rows,_,_=diagram_blocks(d);self.assertIn('库存检查 --有库存--> 交付',rows[-1]['text'])
 def test_missing_connection_fails_instead_of_silently_flattening(self):
  with self.assertRaises(ValueError):diagram_blocks({'elements':{'e':{'name':'linker','from':{'id':'missing'},'to':{'id':'a'}}}})
 def test_workbook_all_sheets_formulas_and_hidden_rows(self):
  from openpyxl import Workbook
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'a.xlsx';b=Workbook();b.active.title='参数';b.active.append(['项目','值']);b.active.append(['上限',60]);s=b.create_sheet('计算');s.sheet_state='hidden';s['A1']='=参数!B2*2';b.save(p)
   rows,_=spreadsheet_blocks(p);text='\n'.join(r[2] for r in rows)
   self.assertIn('B2=60',text);self.assertIn('工作表：计算（hidden）',text);self.assertIn('未提供，不推算结果',text)

if __name__=='__main__':unittest.main()
