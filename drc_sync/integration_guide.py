"""Secret-free PDF attachment generated in memory for each API invitation."""
from io import BytesIO
from xml.sax.saxutils import escape

def guide_pdf(api_url,kinds):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak,Preformatted,KeepTogether
    from reportlab.lib.pagesizes import A4
    from .contract import FIELDS
    output=BytesIO();styles=getSampleStyleSheet();styles.add(ParagraphStyle(name='GuideBody',fontName='Helvetica',fontSize=10,leading=15,spaceAfter=10,textColor=colors.HexColor('#334155')))
    styles.add(ParagraphStyle(name='GuideCode',fontName='Courier',fontSize=8,leading=11,spaceAfter=10))
    styles['Title'].textColor=colors.HexColor('#1c4d88');styles['Title'].alignment=TA_LEFT
    styles['Heading2'].textColor=colors.HexColor('#1c4d88');styles['Heading2'].spaceBefore=12
    def p(text):return Paragraph(text,styles['GuideBody'])
    story=[Paragraph('Chintamani Gems',styles['Title']),p('Read-only inventory API - integration guide'),Spacer(1,8),Paragraph('Connection settings',styles['Heading2'])]
    table=Table([[p('Endpoint'),p(escape(api_url))],[p('HTTP method'),p('GET')],[p('Header'),p('x-api-key: YOUR_API_KEY')],[p('Array data keys'),p('natural, lab_grown')],[p('Inventory scope'),p(escape(', '.join(kinds)))]],colWidths=[115,365])
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(0,-1),colors.HexColor('#f1f5f9')),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),0.4,colors.HexColor('#dce3eb')),('LEFTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),9)]));story.append(table)
    story.extend([Spacer(1,14),p('Use the key from the accompanying invitation email. Keep it on your server; do not place it in browser JavaScript, a public repository or a URL query string.'),Paragraph('Example request',styles['Heading2']),p('<font face="Courier">GET '+escape(api_url)+'</font><br/><font face="Courier">x-api-key: YOUR_API_KEY</font>'),Paragraph('Example response',styles['Heading2'])])
    example='''{
  "natural": [
    {
      "StockId": "NAT-001",
      "Diamond Type": "NATURAL",
      "Shape": "Round",
      "Weight": "1.01",
      "Color": "D",
      "Clarity": "VVS1",
      "PricePerCarat": "5000"
    }
  ],
  "lab_grown": [
    {
      "StockId": "LAB-001",
      "Diamond Type": "LAB_GROWN",
      "Shape": "Oval",
      "Weight": "1.50",
      "Color": "E",
      "Clarity": "VS1",
      "PricePerCarat": "600"
    }
  ]
}'''
    story.append(Preformatted(example,styles['GuideCode']));story.append(PageBreak())
    story.extend([Paragraph('Inventory and pricing',styles['Heading2']),p('Each successful response is a complete snapshot for your company, separated into natural and lab_grown arrays. If a scope is not enabled or empty, its array is returned as []. Natural and Lab Grown records are identified by Diamond Type: NATURAL or LAB_GROWN. Stock IDs remain unchanged. Treat (Diamond Type, StockId) as the record identity if both types are enabled.'),p('PricePerCarat already includes the price adjustment agreed for your company. Do not apply that adjustment again. Prices are rounded to two decimal places. Final Price and Discount are not automatically recalculated by that adjustment.'),p('The API serves the latest successfully published snapshot. When the publisher PC is off, the last snapshot remains available; new local Excel changes are not available until published. Check the X-Inventory-Published-At response header for freshness.'),Paragraph('Fetch, failures and removals',styles['Heading2']),p('Choose a fetch interval with Chintamani Gems. Use GET and your key header for every request. No pagination is required. A successful fetch is not an import confirmation.'),p('Reconcile missing stock only after receiving a successful HTTP 200 response with a valid natural and lab_grown arrays. A 401, 503, timeout, interrupted body or invalid JSON must never be treated as empty inventory or a deletion command. Empty workbook publication is blocked by the publisher.'),p('401 means the key is missing, invalid or revoked. 503 means the feed is temporarily unavailable. Keep your last valid inventory and retry later. Revoked or rotated keys must be replaced through your contact.'),p('Import counts, rejected Stock IDs and rejection reasons require a separate agreed callback or status contract. This endpoint does not claim that your website imported the returned records.'),Paragraph('Response headers',styles['Heading2']),p('X-Inventory-Snapshot identifies the published snapshot. X-Inventory-Records is the number of returned diamonds. X-Inventory-Published-At is the publication time. Cache-Control is private, no-store.'),PageBreak(),Paragraph('Field reference',styles['Heading2']),p('The feed uses the existing supplier field names below. Most values are text, including numeric strings. Optional blank fields are omitted. Your integration should tolerate additional optional fields.')])
    descriptions={'StockId':'Required stock identifier','Diamond Type':'NATURAL or LAB_GROWN','Shape':'Diamond shape','Weight':'Carat weight, positive numeric string','Color':'Color grade','Clarity':'Clarity grade','PricePerCarat':'Positive price per carat; company adjustment already applied'}
    rows=[[p('<b>JSON field</b>'),p('<b>Description</b>')]]
    for field in sorted(FIELDS):rows.append([p(escape(field)),p(escape(descriptions.get(field,'Optional supplier value; omitted when blank')))])
    fields=Table(rows,colWidths=[180,300],repeatRows=1);fields.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf1fa')),('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#dce3eb')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),3)]));story.append(fields)
    def footer(canvas,doc):
        canvas.setStrokeColor(colors.HexColor('#dce3eb'));canvas.line(48,42,A4[0]-48,42);canvas.setFont('Helvetica',8);canvas.setFillColor(colors.HexColor('#64748b'));canvas.drawString(48,28,'Chintamani Gems | Inventory API v1');canvas.drawRightString(A4[0]-48,28,str(doc.page))
    SimpleDocTemplate(output,pagesize=A4,rightMargin=48,leftMargin=48,topMargin=42,bottomMargin=56,title='Chintamani Gems Inventory API Guide',author='Chintamani Gems').build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
