import os
import subprocess
import sys
from time import strftime, localtime
try:
    import pptx as ppt
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-pptx"])
    import pptx as ppt
from pptx.enum.text import MSO_ANCHOR
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.util import Inches, Pt

def decode_seqstr(bname:str, aname:str):
    """Extract state duration, freq, and S2 pwr information from name of file/folder"""
    bname = bname.lower()
    aname = aname.lower()
    if not ('us' in bname and 'us' in aname):
        return None  # state duration missing
    if (bsplit := bname.split('_')) != (asplit := aname.split('_')):  # strip mismatching prefixes
        bsplit = [thisb for (thisb, thisa) in zip(bsplit, asplit) if thisb == thisa]
        if len(bsplit) == 0:
            return None  # no matches found
    while True:  # detect duration
        if bsplit[0].endswith('us'):  # same duration
            dur1 = dur2 = float(bsplit.pop(0)[:-2])
            break
        if bsplit[1].endswith('us'):  # might be different duration
            try:
                dur1 = float(bsplit.pop(0))
            except ValueError:  # prefix ending with _ was removed in last pop
                dur1 = dur2 = float(bsplit.pop(0)[:-2])
                break
            dur2 = float(bsplit.pop(0)[:-2])
            break
        bsplit = bsplit[1:]  # discard prefix and try again
    if bsplit[0].endswith('khz') or bsplit[0].endswith('pct'):
        freq1 = freq2 = float(bsplit.pop(0)[:-3])
    elif bsplit[1].endswith('khz') or bsplit[1].endswith('pct'):
        freq1 = float(bsplit.pop(0))
        freq2 = float(bsplit.pop(0)[:-3])
    else:
        return None  # no frequency found
    pwr2 = float(bsplit.pop(0).removeprefix('s2').removesuffix('w').removesuffix('pct'))
    try:
        chunk = int(bsplit[0])
    except ValueError:
        chunk = None
    return dur1, dur2, freq1, freq2, pwr2, chunk

class MegaPowerPointGenerator:
    r"""Generate comparison PPT for PulseEyeSweep runs. Must use Before and After runs using same test setup.
    Arguments for __init__:
    - SN: Serial number of generator, as string (currently unused)
    - unit_type: Unit model designation, e.g. 3513 (currently unused)
    - before_path: Path to folder containing generated Before plots
    - after_path: Path to folder containing generated After plots (MUST have same organization as before_path)
    - template_path: Path to folder contaning PPT template (try "\\ftcsrv01\Product\RF Delivery\Everest\DVT\Connor Misc Tests")
    - fname: Filename to save presentation as (defaults to "eVerest.pptx")"""
    def __init__(self, SN:str, unit_type:str, before_path:str|os.PathLike[str], after_path:str|os.PathLike[str],
            template_path:str|os.PathLike[str], fname:str='eVerest.pptx'):
        self.author = str(os.getenv('username')).replace(".", " ")
        self.SN = SN
        self.unit_type = unit_type
        self.before_path = before_path
        self.after_path = after_path
        self.template_path = template_path# r'C:\Users\connor.wallis\Desktop\PowerPoints'
        template_filename = 'eVerest_blank_template.pptx'

        self.transitions = set()
        self.before_plots = {}
        self.after_plots = {}

        allplots = zip(os.walk(self.before_path), os.walk(self.after_path))
        # print(tests)
        seqsetup = ...
        for (bdir, adir) in allplots:
            bpngs:list[str] = [f for f in bdir[2] if f[-4:] == ".png" and "fastDAQ" not in f]
            apngs:list[str] = [f for f in adir[2] if f[-4:] == ".png" and "fastDAQ" not in f]
            if len(bpngs) != len(apngs):
                print(f"[WARNING] Image count mismatch: {len(bpngs)} != {len(apngs)}. PPT slide for {bdir[0]} may be malformed.")
            if not bpngs or not apngs:
                continue  # no .png files to check in this directory
            bfolder = None
            if 'us' in bdir[0] or 'us' in adir[0]:
                bfolder = next((f for f in bdir[0].split(os.path.sep) if 'us_' in f and '_S2' in f), None)
                afolder = next((f for f in adir[0].split(os.path.sep) if 'us_' in f and '_S2' in f), None)
                if bfolder is not None and afolder is not None:
                    seqsetup = decode_seqstr(bfolder, afolder)
                    if seqsetup is None:
                        print(f"[ERROR] Folder name invalid for {bdir[0]} or {adir[0]}")
                        continue
                    self.transitions.add(seqsetup)
                elif bfolder is not None or afolder is not None:
                    print(f"[ERROR] Folder name mismatch between {bdir[0]} and {adir[0]}")
                    continue
            for (bfile, afile) in zip(bpngs, apngs):
                if bfolder is None:
                    seqsetup = decode_seqstr(bfile, afile)
                    if seqsetup is None:
                        print(f"[ERROR] Filename invalid for {os.path.join(bdir[0],bfile)} or {os.path.join(adir[0],afile)}")
                        continue
                    self.transitions.add(seqsetup)
                if seqsetup not in self.before_plots:
                    self.before_plots[seqsetup] = []
                    self.after_plots[seqsetup] = []
                self.before_plots[seqsetup].append(os.path.join(bdir[0],bfile))
                self.after_plots[seqsetup].append(os.path.join(adir[0],afile))

        today = f"{strftime('%B',localtime())} {strftime('%d', localtime())}, {strftime('%Y', localtime())}"

        self.new_presentation_filename = fname
        self.new_presentation = ppt.Presentation(os.path.join(self.template_path,template_filename))

        if os.path.isfile(os.path.join(self.template_path,self.new_presentation_filename)):
            self.new_presentation = ppt.Presentation(os.path.join(self.template_path,self.new_presentation_filename))
            return
        self.new_presentation = ppt.Presentation(os.path.join(self.template_path,template_filename))
        self.new_presentation.save(os.path.join(self.template_path,self.new_presentation_filename))

        ################# Title Slide ###################
        title_slide_shapes = self.new_presentation.slides[0].shapes
        left_title = Inches(0.45)
        top_title = Inches(2)
        width_title = Inches(5.5)
        height_title = Inches(3)
        title = title_slide_shapes.add_textbox(left_title, top_title, width_title, height_title)
        title_text = title.text_frame.add_paragraph()
        title_text.text = 'eVerest {}' # FIXME
        title_text.font.size = Pt(28)
        title_text.font.bold = True
        title.text_frame.word_wrap = True
        title.text_frame.vertical_anchor = MSO_ANCHOR.TOP
        title_text.font.color.theme_color = MSO_THEME_COLOR.TEXT_1

        left_author = Inches(0.416)
        top_author = Inches(5.15)
        width_author = Inches(3)
        height_author = Inches(0.5)
        author_shape = title_slide_shapes.add_textbox(left_author, top_author, width_author, height_author)
        author_text = author_shape.text_frame.add_paragraph()
        author_text.text = self.author
        author_text.font.size = Pt(14)
        author_text.font.bold = True
        author_text.font.color.theme_color = MSO_THEME_COLOR.ACCENT_1

        left_date = Inches(0.416)
        top_date = Inches(5.37)
        width_date = Inches(3)
        height_date = Inches(0.5)
        date_shape = title_slide_shapes.add_textbox(left_date, top_date, width_date, height_date)
        date_text = date_shape.text_frame.add_paragraph()
        date_text.text = today
        date_text.font.size = Pt(14)
        date_text.font.bold = False
        date_text.font.color.theme_color = MSO_THEME_COLOR.TEXT_2

    #######################################################################################################################
    def addSlide(self, seqsp:tuple[float,float,float,float,float,int|None], before_plot:str, after_plot:str, autosave:bool=True):
        """Add slide to presentation.
        Arguments:
        - seqsp: Setpoints for this transition: tuple (S1 dur, S2 dur, S1 freq, S2 freq, S2 pwr, chunk (None for summary))
        - before_plot: Path to image to use for Before plot
        - after_plot: Path to image to use for After plot
        - autosave: If True, save presentation to file after creating slide. Set to False to speed up bulk slide adding."""
        seqfname = next(f for f in before_plot.split(os.path.sep) if 'us_' in f and '_S2' in f)
        self.new_presentation.slides.add_slide(self.new_presentation.slide_layouts[22])
        _slide = self.new_presentation.slides[-1].shapes
        slide_title = _slide.title
        slide_title.text = "dur " + (f"{seqsp[0]}" if seqsp[0] == seqsp[1] else f"[{seqsp[0]}, {seqsp[1]}]") + " us, freq "\
            + (f"{seqsp[2]}" if seqsp[2] == seqsp[3] else f"[{seqsp[2]}, {seqsp[3]}]") + (" kHz" if "kHz" in seqfname else "%")\
            + (f", S2 pwr {seqsp[4]}" + " W" if "W_" in seqfname else "%")
        if seqsp[5] is not None:
            slide_title.text += f", chunk {seqsp[5]}"
        print("Making slide for "+slide_title.text)

        text_box_shape = _slide.add_textbox(Inches(2.84), Inches(1.2), Inches(0.75), Inches(0.25))
        text_box_text = text_box_shape.text_frame.add_paragraph()
        text_box_text.text = "Before "+before_plot.split(os.path.sep)[-1].split('_')[-1].removesuffix('.png')
        text_box_text.font.size = Pt(18)
        text_box_text.font.bold = True
        text_box_text.font.underline = True
        text_box_text.font.color.theme_color = MSO_THEME_COLOR.TEXT_1

        text_box_shape2 = _slide.add_textbox(Inches(9.56), Inches(1.2), Inches(0.75), Inches(0.25))
        text_box_text2 = text_box_shape2.text_frame.add_paragraph()
        text_box_text2.text = "After "+after_plot.split(os.path.sep)[-1].split('_')[-1].removesuffix('.png')
        text_box_text2.font.size = Pt(18)
        text_box_text2.font.bold = True
        text_box_text2.font.underline = True
        text_box_text2.font.color.theme_color = MSO_THEME_COLOR.TEXT_1

        text_box_shape3 = _slide.add_textbox(Inches(0.2), Inches(6.65), Inches(2), Inches(1))
        text_box_text3 = text_box_shape3.text_frame.add_paragraph()
        text_box_text3.text = 'Advanced Energy Confidential Information\n© Advanced Energy Industries 2025'
        text_box_text3.font.size = Pt(10)
        text_box_text3.font.name = 'Arial (Headings)'
        text_box_text3.font.color.theme_color = MSO_THEME_COLOR.TEXT_2

        _slide.add_picture(before_plot, Inches(0.23), Inches(1.875), width=Inches(6.25), height=Inches(4.75))
        _slide.add_picture(after_plot, Inches(6.9), Inches(1.875), width=Inches(6.25), height=Inches(4.75))

        if autosave:
            self.save()
    #######################################################################################################################

    def makeAllSlides(self):
        """Add all prepared slides to presentation, sorted in order of state dur, freq, state 2 pwr, chunk.
        Summary plots for each transition are sorted before individual PulseEye plots.
        Automatically save to disk after creating all slides."""
        for sp in sorted(self.transitions, key=lambda x: [-1 if v is None else v for v in x]):
            for (bplot, aplot) in zip(self.before_plots[sp], self.after_plots[sp]):
                self.addSlide(sp, bplot, aplot, False)
        self.save()

    #######################################################################################################################
    def save(self, sfile:str|os.PathLike[str]|None=None):
        """Save presentation to specified filename in After folder."""
        if sfile is not None:
            self.new_presentation_filename = sfile
        self.new_presentation.save(os.path.join(self.after_path,self.new_presentation_filename))
    #######################################################################################################################

if __name__ == "__main__":
    test = MegaPowerPointGenerator('12345678', '3513',
        r"testData\PulseEyeSweep\20251110_3540_2", r"testData\PulseEyeSweep\20251110_3540_3",
        r"Z:\Everest\DVT\Connor Misc Tests")
    test.makeAllSlides()
