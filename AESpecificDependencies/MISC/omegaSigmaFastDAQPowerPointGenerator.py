import os
import subprocess
import sys
from time import strftime, localtime
# from datetime import date
import numpy as np
try:
    import pptx as ppt
    # from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR
    from pptx.enum.dml import MSO_THEME_COLOR
    from pptx.util import Inches, Pt
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-pptx"])
finally:
    import pptx as ppt
    # from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR#, MSO_AUTO_SIZE, PP_ALIGN
    from pptx.enum.dml import MSO_THEME_COLOR
    from pptx.util import Inches, Pt

# Enter SN and unit type at top
class MegaPowerPointGenerator:
    def __init__(self, SN, unit_type, data_path, template_path):
        self.author = str(os.getenv('username')).replace(".", " ")
        self.SN = SN
        self.unit_type = unit_type
        self.data_path = data_path # test plots
        self.template_path = template_path# r'C:\Users\connor.wallis\Desktop\PowerPoints'
        template_filename = r'\eVerest_blank_template.pptx'

        self.plot_order_cw = []
        self.before_plots_cw = {}
        self.after_plots_cw = {}

        self.plot_order_dlp_sp1 = []
        self.plot_order_dlp_sp2 = []
        self.before_plots_dlp = {}
        self.after_plots_dlp = {}

        tests = os.listdir(self.data_path)
        print(tests)
        for tdir in tests:
            if os.path.isfile(self.data_path + r'\\' + tdir):
                pwr1 = int(tdir.split('_')[2].split('.')[0][:-1])
                if tdir.split('_')[0]=='After':
                    if tdir.split('_')[1]=='CW':
                        self.plot_order_cw.append(pwr1)
                        self.after_plots_cw[pwr1] = tdir
                    elif tdir.split('_')[1]=='DLP':
                        pwr2 = int(tdir.split('_')[3].split('.')[0][:-1])
                        self.plot_order_dlp_sp1.append(pwr1)
                        self.plot_order_dlp_sp2.append(pwr2)
                        try:
                            if len(self.after_plots_dlp[pwr1]) > 0:
                                self.after_plots_dlp[pwr1][pwr2] = tdir
                        except:
                            self.after_plots_dlp[pwr1] = {}
                            self.after_plots_dlp[pwr1][pwr2] = tdir

                elif tdir.split('_')[0]=='Before':
                    if tdir.split('_')[1]=='CW':
                        self.before_plots_cw[pwr1] = tdir
                    elif tdir.split('_')[1]=='DLP':
                        pwr2 = int(tdir.split('_')[3].split('.')[0][:-1])
                        try:
                            if len(self.before_plots_dlp[pwr1]) > 0:
                                self.before_plots_dlp[pwr1][pwr2] = tdir
                        except:
                            self.before_plots_dlp[pwr1] = {}
                            self.before_plots_dlp[pwr1][pwr2] = tdir
                    
        self.plot_order_cw = sorted(self.plot_order_cw)
        print(self.plot_order_cw)
        self.plot_order_dlp_sp1 = np.unique(sorted(self.plot_order_dlp_sp1))
        self.plot_order_dlp_sp2 = np.unique(sorted(self.plot_order_dlp_sp2))
        today = f"{strftime('%B',localtime())} {strftime('%d', localtime())}, {strftime('%Y', localtime())}"

        self.new_presentation_filename = r'\eVerest.pptx'
        self.new_presentation = ppt.Presentation(self.template_path+template_filename)

        if os.path.isfile(self.template_path+self.new_presentation_filename):
            self.new_presentation = ppt.Presentation(self.template_path+self.new_presentation_filename)
            return
        else:
            self.new_presentation = ppt.Presentation(self.template_path+template_filename)
            self.new_presentation.save(self.template_path+self.new_presentation_filename)

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

            # ################# Test List Slide ###################
            # test_list_shapes = self.new_presentation.slides[1].shapes

            # for test in range(len(self.slides)):
            #     # if test < int(len(self.slides)/2):
            #     left_num = Inches(0.5)
            #     top_num = Inches(1) + Inches(test)
            #     width_num = Inches(0.75)
            #     height_num = Inches(0.75)
            #     left_testname = Inches(1.15)
            #     top_testname = Inches(1.2) + Inches(test)
            #     width_testname = Inches(2)
            #     height_testname = Inches(0.75)
            #     # else:
            #     #     left_num = Inches(6)
            #     #     top_num = Inches(1) + Inches(test - int(len(self.slides)/2))
            #     #     width_num = Inches(0.75)
            #     #     height_num = Inches(0.75)
            #     #     left_testname = Inches(6.75)
            #     #     top_testname = Inches(1.2) + Inches(test - int(len(self.slides)/2))
            #     #     width_testname = Inches(2)
            #     #     height_testname = Inches(0.75)

            #     test_number_shape = test_list_shapes.add_textbox(left_num, top_num, width_num, height_num)
            #     test_number_text = test_number_shape.text_frame.add_paragraph()
            #     test_number_text.text = str(test+1) + '.'
            #     test_number_text.font.size = Pt(40)
            #     test_number_text.font.bold = True
            #     test_number_text.font.color.theme_color = MSO_THEME_COLOR.ACCENT_1
            #     test_number_name = test_list_shapes.add_textbox(left_testname, top_testname, width_testname, height_testname)
            #     test_number_name_text = test_number_name.text_frame.add_paragraph()
            #     title = '{} {} {} {}'.format(self.slides[test].split('_')[0], self.slides[test].split('_')[1], self.slides[test].split('_')[2], self.slides[test].split('_')[3])
            #     test_number_name_text.text = title
            #     test_number_name_text.font.size = Pt(22)
            #     test_number_name_text.font.color.theme_color = MSO_THEME_COLOR.TEXT_1

            #     self.new_presentation.save(self.template_path+self.new_presentation_filename)



    #######################################################################################################################
    def addFDSlide(self, before_plot:str, after_plot:str):
        self.new_presentation.slides.add_slide(self.new_presentation.slide_layouts[22])
        _slide = self.new_presentation.slides[-1].shapes
        slide_title = _slide.title
        if before_plot.split('_')[1]=='CW':
            slide_title.text = f"{before_plot.split('_')[1]} {before_plot.split('_')[2].split('.')[0]}"
            print("Making slide for "+slide_title.text)
        else:
            slide_title.text = f"{before_plot.split('_')[1]} {before_plot.split('_')[2]}, {before_plot.split('_')[3].split('.')[0]}"
            print("Making slide for "+slide_title.text)
        
        text_box_shape = _slide.add_textbox(Inches(2.84), Inches(1.2), Inches(0.75), Inches(0.25))
        text_box_text = text_box_shape.text_frame.add_paragraph()
        text_box_text.text = before_plot.split('_')[0]
        text_box_text.font.size = Pt(18)
        text_box_text.font.bold = True
        text_box_text.font.underline = True
        text_box_text.font.color.theme_color = MSO_THEME_COLOR.TEXT_1

        text_box_shape2 = _slide.add_textbox(Inches(9.56), Inches(1.2), Inches(0.75), Inches(0.25))
        text_box_text2 = text_box_shape2.text_frame.add_paragraph()
        text_box_text2.text = after_plot.split('_')[0]
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

        _slide.add_picture(self.data_path + r'\\' + before_plot, Inches(0.23), Inches(1.875), width=Inches(6.25), height=Inches(4.75))
        _slide.add_picture(self.data_path + r'\\' + after_plot, Inches(6.9), Inches(1.875), width=Inches(6.25), height=Inches(4.75))
        
        self.save()
    #######################################################################################################################


    def makeAllSlides(self):
        for sp in self.plot_order_cw:
            self.addFDSlide(self.before_plots_cw[sp], self.after_plots_cw[sp])
        for sp1 in self.plot_order_dlp_sp1:
            for sp2 in self.plot_order_dlp_sp2:
                self.addFDSlide(self.before_plots_dlp[sp1][sp2], self.after_plots_dlp[sp1][sp2])


    #######################################################################################################################
    def save(self):
        self.new_presentation.save(self.template_path+self.new_presentation_filename)
    #######################################################################################################################



# test = MegaPowerPointGenerator()
# test.addFDSlide(r'Before_CW_0W.png', r'After_CW_0W.png')
# test.makeAllSlides()