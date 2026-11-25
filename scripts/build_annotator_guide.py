"""Create the participant-facing CLARITY annotation guide."""

from pathlib import Path
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle, HRFlowable,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs/CLARITY-Annotator-Guide.pdf'
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

FONT_DIR = Path('/System/Library/Fonts/Supplemental')
font_files = ['Arial.ttf', 'Arial Bold.ttf', 'Arial Italic.ttf', 'Arial Bold Italic.ttf']
if not (FONT_DIR / font_files[0]).is_file():
    FONT_DIR = Path('/usr/share/fonts/truetype/dejavu')
    font_files = ['DejaVuSans.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSans-Oblique.ttf', 'DejaVuSans-BoldOblique.ttf']
for name, filename in zip(['Guide', 'Guide-Bold', 'Guide-Italic', 'Guide-BoldItalic'], font_files):
    pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / filename)))
pdfmetrics.registerFontFamily('Guide', normal='Guide', bold='Guide-Bold',
                            italic='Guide-Italic', boldItalic='Guide-BoldItalic')

INK = colors.HexColor('#182E36')
TEAL = colors.HexColor('#173F47')
MUTED = colors.HexColor('#53666B')
LINE = colors.HexColor('#D7E0DF')
PALE = colors.HexColor('#F3F6F4')
GREEN = colors.HexColor('#2F6B50')
WARM = colors.HexColor('#8B533E')
BLUE = colors.HexColor('#365F87')

WIDTH, HEIGHT = A4
MARGIN = 48
BODY_WIDTH = WIDTH - 2 * MARGIN

STYLES = {
    'title': ParagraphStyle('title', fontName='Guide-Bold', fontSize=30, leading=33,
                            textColor=TEAL, spaceAfter=10),
    'heading': ParagraphStyle('heading', fontName='Guide-Bold', fontSize=23, leading=28,
                              textColor=TEAL, spaceAfter=10),
    'intro': ParagraphStyle('intro', fontName='Guide', fontSize=11.3, leading=16,
                            textColor=MUTED, spaceAfter=15),
    'body': ParagraphStyle('body', fontName='Guide', fontSize=10.8, leading=15,
                           textColor=INK, spaceAfter=7),
    'small': ParagraphStyle('small', fontName='Guide', fontSize=9.2, leading=12.5,
                            textColor=MUTED, spaceAfter=5),
    'section': ParagraphStyle('section', fontName='Guide-Bold', fontSize=13, leading=17,
                              textColor=TEAL, spaceBefore=8, spaceAfter=7),
    'label': ParagraphStyle('label', fontName='Guide-Bold', fontSize=17, leading=21,
                            textColor=TEAL, spaceAfter=7),
    'example': ParagraphStyle('example', fontName='Guide', fontSize=10.8, leading=15,
                              textColor=INK, spaceAfter=4),
    'step_title': ParagraphStyle('step_title', fontName='Guide-Bold', fontSize=11, leading=15,
                                 textColor=TEAL, spaceAfter=3),
    'number': ParagraphStyle('number', fontName='Guide-Bold', fontSize=17, leading=21,
                             textColor=TEAL),
    'table': ParagraphStyle('table', fontName='Guide', fontSize=10, leading=14,
                            textColor=INK, spaceAfter=0),
    'comparison': ParagraphStyle('comparison', fontName='Guide', fontSize=10.2, leading=14,
                                 textColor=INK, spaceAfter=0),
    'comparison_name': ParagraphStyle('comparison_name', fontName='Guide-Bold', fontSize=10.2, leading=14,
                                      textColor=TEAL, spaceAfter=0),
    'check': ParagraphStyle('check', fontName='Guide', fontSize=10.5, leading=15,
                            textColor=INK, leftIndent=15, firstLineIndent=-15,
                            spaceAfter=4),
}


def p(text, style='body'):
    return Paragraph(text, STYLES[style])


def gap(height=8):
    return Spacer(1, height)


def rule():
    return HRFlowable(width='100%', thickness=0.6, color=LINE, spaceBefore=6, spaceAfter=15)


def note(text):
    box = Table([[p(text)]], colWidths=[BODY_WIDTH])
    box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), PALE),
        ('LINEBEFORE', (0, 0), (0, -1), 2, TEAL),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    return box


def step(number, title, body):
    content = [p(title, 'step_title'), p(body)]
    row = Table([[p(str(number).zfill(2), 'number'), content]], colWidths=[36, BODY_WIDTH - 36])
    row.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    return row


LABELS = [
    {
        'name': 'Explicit', 'group': 'CLEAR REPLY', 'color': GREEN,
        'definition': 'The requested information is explicitly stated in the requested form.',
        'question': 'Do you have your own views on proportional representation at Westminster?',
        'answer': 'Yes.',
        'why': 'The speaker directly confirms the information requested by the question.',
        'remember': 'A short reply can be complete. Do not require an explanation when the question does not request one.',
    },
    {
        'name': 'Implicit', 'group': 'AMBIVALENT REPLY', 'color': WARM,
        'definition': 'The requested information is conveyed indirectly and requires inference.',
        'question': 'Will you watch television?',
        'answer': 'What else is there to do?',
        'why': 'The reply suggests an intention to watch television, without directly confirming it.',
        'remember': 'A response phrased as a question can convey an answer. Clarification applies when the speaker asks what the interviewer means.',
    },
    {
        'name': 'General', 'group': 'AMBIVALENT REPLY', 'color': WARM,
        'definition': 'The response is too general to provide the requested specificity.',
        'question': 'Which film is your favourite?',
        'answer': 'Fight Club, Filth, and Hereditary.',
        'why': 'The question asks for a single favourite. Listing several films leaves that choice unresolved.',
        'remember': 'Being on topic is not enough if the requested level of specificity is missing.',
    },
    {
        'name': 'Partial/half-answer', 'group': 'AMBIVALENT REPLY', 'color': WARM,
        'definition': 'The response provides only part of the requested information.',
        'question': 'Did you like the film?',
        'answer': 'Its direction was excellent.',
        'why': 'The reply evaluates one aspect of the film, while the question asks about the film overall.',
        'remember': 'Judge coverage within the current sub-question. The same full response may address other sub-questions differently.',
    },
    {
        'name': 'Dodging', 'group': 'AMBIVALENT REPLY', 'color': WARM,
        'definition': 'The response ignores the question and addresses another topic.',
        'question': 'Do you like this new dress?',
        'answer': 'We are late.',
        'why': 'The response moves straight to a different topic without addressing the requested opinion.',
        'remember': 'An explicit refusal is Declining to answer. Dodging describes a response that bypasses the question.',
    },
    {
        'name': 'Deflection', 'group': 'AMBIVALENT REPLY', 'color': WARM,
        'definition': 'The response begins on topic, then shifts focus away from the requested information.',
        'question': 'Was it you who ate the final slice of pie?',
        'answer': 'That was an excellent recipe. I always enjoy recipes with chocolate chips.',
        'why': 'The speaker uses the pie as a starting point but turns to the recipe instead of saying who ate the slice.',
        'remember': 'Look for the shift in focus. A related subject does not automatically provide the information requested.',
    },
    {
        'name': 'Declining to answer', 'group': 'CLEAR NON-REPLY', 'color': BLUE,
        'definition': 'The speaker acknowledges the question and refuses to answer it.',
        'question': 'Would you regard the hypothetical outcome we discussed as a defeat?',
        'answer': "I won't predict future events.",
        'why': 'The speaker withholds an answer by refusing to speculate about the outcome.',
        'remember': 'The refusal may be expressed directly or indirectly. Identify the refusal from the response, without guessing the motive.',
    },
    {
        'name': 'Claims ignorance', 'group': 'CLEAR NON-REPLY', 'color': BLUE,
        'definition': 'The speaker states that they do not know the requested information.',
        'question': 'On what date did the government order the refit of HMAS Kanimbla?',
        'answer': "I don't know the date. I will check and inform Parliament.",
        'why': 'The speaker acknowledges lacking the information. A promise to find out does not supply the date now.',
        'remember': 'Use the stated lack of knowledge. Do not decide whether the speaker actually knows the answer.',
    },
    {
        'name': 'Clarification', 'group': 'CLEAR NON-REPLY', 'color': BLUE,
        'definition': 'The speaker asks for clarification instead of an answer.',
        'question': 'Did you decide to release the fund?',
        'answer': 'Are you referring to the public fund?',
        'why': 'The speaker requests a more precise question and has not yet provided the requested decision information.',
        'remember': 'A question-shaped response is not sufficient on its own. Check whether it asks what the original question means.',
    },
]


def label_block(item):
    heading = (
        '<b>' + escape(item['name']) + '</b>'
        + ' <font size="8.5" color="' + item['color'].hexval() + '">  '
        + item['group'] + '</font>'
    )
    example = Table([
        [p('<b>Q</b>  ' + escape(item['question']), 'example')],
        [p('<b>A</b>  ' + escape(item['answer']), 'example')],
    ], colWidths=[BODY_WIDTH])
    example.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), PALE),
        ('LEFTPADDING', (0, 0), (-1, -1), 11),
        ('RIGHTPADDING', (0, 0), (-1, -1), 11),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 1), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 7),
    ]))
    return KeepTogether([
        p(heading, 'label'),
        p(item['definition']),
        gap(2), example, gap(7),
        p('<b>Why this label:</b> ' + item['why']),
        p(item['remember'], 'small'),
        gap(7),
    ])


PAGES = []

page1 = [
    p('CLARITY<br/>Annotator guide', 'title'),
    p('Political question-answer exchanges<br/>Instructions and label examples', 'intro'),
    note('<b>Your task:</b> identify how the original response addresses the information requested by each sub-question. '
         'Base your label on the original question and answer.'),
    gap(12),
    p('What you will see', 'section'),
    p('Each task shows one <b>complete original question-answer exchange</b>, followed by all its '
      '<b>numbered sub-questions and answer summaries</b>. Each sub-question represents one request for information. '
      'Use the original exchange to check the summaries and decide each response label.'),
    p('For each task', 'section'),
    step(1, 'Read the complete exchange',
         'Read the original question and answer. Identify the information requested by each numbered sub-question.'),
    step(2, 'Match the numbered parts',
         'Question 1 corresponds to answer summary 1, question 2 to summary 2, and so on. '
         'Assess each request separately.'),
    step(3, 'Highlight and label each answer',
         'Select one response label, then highlight the <b>entire numbered answer summary</b>. '
         'Assign exactly one label to every numbered summary. Different parts can receive different labels.'),
    step(4, 'Check the proposed questions and summaries',
         'If a sub-question is absent from the original question, highlight its answer summary with '
         '<b>Error: Question does not exist in the original text</b>. This is a decomposition flag. '
         'Report inaccurate summaries in the corrections field and base response labels on the original answer.'),
    step(5, 'Add missing questions and review',
         'In <b>Any additional missed questions or corrections?</b>, enter each missing question on a separate line '
         'as <b>Question: Label</b>. Review all numbered summaries before submitting.'),
    gap(1),
    p('<b>Annotation focus:</b> assess what the response communicates in context. Do not choose a label '
      'because you agree with a political position, or because you assume the speaker is honest or dishonest.', 'small'),
]
PAGES.append(page1)

for title, subtitle, indices in [
    ('Direct and indirect replies',
     'A direct answer, an inferable answer, and a response that lacks the requested specificity.', [0, 1, 2]),
    ('Incomplete and diverted replies',
     'A response may cover only part of a request, bypass it, or use its topic to shift focus.', [3, 4, 5]),
    ('Clear non-replies',
     'The speaker withholds an answer, states that they do not know, or asks for clarification.', [6, 7, 8]),
]:
    flow = [p(title, 'heading'), p(subtitle, 'intro')]
    for position, index in enumerate(indices):
        if position:
            flow.append(rule())
        flow.append(label_block(LABELS[index]))
    flow.extend([gap(3), p('Examples adapted from Table 4 of Thomas et al. (2024). Wording is simplified or abridged; '
                          'the situations and label assignments follow the paper.', 'small')])
    PAGES.append(flow)

comparisons = [
    ('Explicit / Implicit',
     'Explicit gives the requested information directly. Implicit conveys it through an inference. '
     'Judge the relationship between the request and the response, not the response length.'),
    ('Implicit / General',
     'Implicit allows the requested information to be inferred. General leaves the requested specificity unresolved.'),
    ('General / Partial/half-answer',
     'General lacks the requested precision. Partial/half-answer covers only a component of what the current sub-question requests.'),
    ('Dodging / Deflection',
     'Dodging bypasses the question and moves straight to another topic. Deflection engages with the topic '
     'but shifts attention to a different point.'),
    ('Dodging / Declining to answer',
     'Dodging bypasses the request. Declining to answer communicates a refusal to provide the requested information.'),
    ('Declining to answer / Claims ignorance',
     'A refusal to provide information differs from stating that the information is unknown to the speaker.'),
]
page5 = [
    p('Consistent decisions', 'heading'),
    p('Use these distinctions when two labels appear plausible. Return to the exact request and the full response.', 'intro'),
]
comparison_table = Table(
    [[p(pair, 'comparison_name'), p(explanation, 'comparison')] for pair, explanation in comparisons],
    colWidths=[150, BODY_WIDTH - 150],
)
comparison_table.setStyle(TableStyle([
    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ('LEFTPADDING', (0, 0), (0, -1), 0),
    ('LEFTPADDING', (1, 0), (1, -1), 10),
    ('RIGHTPADDING', (0, 0), (0, -1), 10),
    ('RIGHTPADDING', (1, 0), (1, -1), 0),
    ('TOPPADDING', (0, 0), (-1, -1), 7),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ('LINEBELOW', (0, 0), (-1, -2), 0.4, LINE),
]))
page5.extend([comparison_table, gap(12)])
page5.extend([
    note('<b>Do not require unrequested detail.</b> If the question does not ask for elaboration, '
         'a lack of elaboration is not, by itself, missing information.'),
    gap(10),
    p('Corrections and uncertainty', 'section'),
    p('Keep corrections in the <b>language of the exchange</b>. Preserve negation, uncertainty and qualifications. '
      'Use the exact English label names in the interface. Describe uncertainty or an inaccurate summary '
      'in the corrections field. If the response-type decision remains unresolved, contact the coordinator '
      'rather than choosing a label only to complete the item.'),
    p('Before you submit', 'section'),
    p('1. I read the original exchange and assessed every numbered sub-question.', 'check'),
    p('2. Every numbered answer summary has one highlight and one label based on the source text.', 'check'),
    p('3. I recorded necessary corrections or missing questions.', 'check'),
    p('4. I worked independently and did not consult another annotator\'s answers.', 'check'),
    gap(10),
    p('<b>Source and scope</b><br/>Adapted from Thomas, Filandrianos, Lymperaiou, Zerva and Stamou (2024), '
      '<i>I Never Said That: A dataset, taxonomy and baselines on response clarity classification</i>, '
      'Findings of EMNLP 2024. Definitions and examples follow Section 3 and Table 4. '
      'The workflow follows the annotation procedure, with field names matched to the current interface. '
      '<link href="https://aclanthology.org/2024.findings-emnlp.300.pdf#page=18" color="#173F47">'
      'Read Table 4 in the paper</link>.', 'small'),
])
PAGES.append(page5)

PAGE_NAMES = [
    'Your annotation workflow', 'Direct and indirect replies', 'Incomplete and diverted replies',
    'Clear non-replies', 'Consistent decisions and final checklist',
]


def decorate(canvas, doc):
    canvas.saveState()
    canvas.setTitle('CLARITY Annotator Guide')
    canvas.setAuthor('CLARITY Research Team')
    canvas.setSubject('Instructions for annotating political response clarity')
    canvas.setKeywords('CLARITY, annotation, political discourse, response clarity, Label Studio')
    canvas.setFillColor(TEAL)
    canvas.setFont('Guide-Bold', 12)
    canvas.drawString(MARGIN, HEIGHT - 37, 'CLARITY')
    canvas.setFillColor(MUTED)
    canvas.setFont('Guide', 8.5)
    canvas.drawRightString(WIDTH - MARGIN, HEIGHT - 36, 'ANNOTATOR GUIDE')
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, HEIGHT - 49, WIDTH - MARGIN, HEIGHT - 49)
    canvas.line(MARGIN, 39, WIDTH - MARGIN, 39)
    canvas.setFont('Guide', 8)
    label = PAGE_NAMES[doc.page - 1] if doc.page <= len(PAGE_NAMES) else 'Annotation guide'
    canvas.drawString(MARGIN, 25, label)
    canvas.drawRightString(WIDTH - MARGIN, 25, '{:02d} / 05'.format(doc.page))
    canvas.bookmarkPage('page-{}'.format(doc.page))
    canvas.addOutlineEntry(label, 'page-{}'.format(doc.page), level=0, closed=False)
    canvas.restoreState()


def build():
    doc = BaseDocTemplate(str(OUTPUT), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=72, bottomMargin=52, title='CLARITY Annotator Guide',
                          author='CLARITY Research Team', allowSplitting=1)
    frame = Frame(MARGIN, 52, BODY_WIDTH, HEIGHT - 72 - 52, leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0, id='body')
    doc.addPageTemplates([PageTemplate(id='guide', frames=[frame], onPage=decorate)])
    story = []
    for index, page in enumerate(PAGES):
        if index:
            story.append(PageBreak())
        story.extend(page)
    doc.build(story)
    resource = ROOT / 'src/clarity_qa/resources/CLARITY-Annotator-Guide.pdf'
    resource.parent.mkdir(parents=True, exist_ok=True)
    resource.write_bytes(OUTPUT.read_bytes())
    print(OUTPUT)


if __name__ == '__main__':
    build()
