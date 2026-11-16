"""Unified offline analysis launcher for T5, T6, T7, and T8.

This file embeds the four validated standalone analysis scripts and executes the
selected workflow in an isolated namespace. The numerical algorithms, waveform
pairing rules, thresholds, plots, CSV columns, and failure handling remain those
of the corresponding standalone script. The launcher only overrides the input
folder and the parent folder where that workflow creates its normal timestamped
results directory.
"""
from __future__ import annotations

import ast
import base64
import traceback
import types
import zlib
from pathlib import Path
from tkinter import Tk, filedialog, messagebox, simpledialog

ANALYSIS_SOURCES = {
    'T5': 'c-pl*e{<VLuK#zY-(ipU@=7K&W4YsAqg2WG9OvR0$IjTgbZ%5V4lS-MUMNySQnpl|zxxBQzbQ&~(q1~XB`y}g0$4171?_p>#dMly1!u!UmM0A_CTo@yDZ6~X$BSE@mkZ9StmZGLNuKXD*_^|0lCSHmj=uly`|plF^jKE2W+tAOH>~E(qRfhhP0INkP(|$c!-0aSk~=`n?vh(REvq@8IFm$|HLn?BJKTT9nv$TWSyeZz2M@|AJwQyFt)@zfI%~4B05q4tBa`f<44D<*uFGbIxCslOeTBdDM7F5Pn~K-<YX<DFgeGRi&G+9a4nv?@!t~oRZ;~4zF$3^fnWr_ZuY%8u66i_9NLq56%olmqEK?4=d7k(EchjnzvpAkEn`Oo0m}T=tSv4@FD4PU%sZpHr>7>5ZpDV6E0^<{YolL$+bScP=Rw>8TcR9RBnpvJ*tI<#JRgN@YKta4xBf~Et$U5g~h8nc^m?zC5FB?D-E!OynB{f^*jRq)|^Tiq;77K~mo{}_^8EDnepC|_ENw!!=b#?<bqsC`he$A^!T2*}f&&#We%lQ4}(ed!~;+zfGess7m#y(x1#3%3maT5P}dj9U>R~Ym64)^!@-cKQe5AZvXfEQQ8Hsqmz6Yv0ca{l?`?Bdf&e0qL5JUu$wiBto>KRtIq734t#zkjqLuRdN}3_qNn|0BMlMc^7PFRrfe$)}UctJACD$@%dKjrMT%PXKW7@#E3?yZGz`ki^4_vy;msg!xPuempb~Lm0R^JvkqCLVY^B7{>2Dog!)!9{!i|DVxknu<AG~7Rx4{f;scb57^!tGLruH-!b@Cm7wnd7>6IcrZ3T5*344a!hRqiYM%2+!&87V<`X6Lmo%$jpsdz@lQlUXcvl3;1SQxxMU!L&Y&3K`^#phG@zrNSQhR|RAEmr*s&cIeW>W^+Mbnjx5_EzN;Br}@{hw4-S^3_1$!LL!teO^S5*#*TL2LHaU|@t)@g$9jyBId4kK3tFz$B?=xybpbSVTpdR8_JLnfW}{BD||XIC)k#KA?yYBS;!CMS!;Oy~=}m{0?34z=O}=3(FJ)Gw_zpbnoXq*dq887gP_tiZ60-0bUTe0zo>vg)#L`=8I;HV(znhVxUOo{6UFgMw|h!%?f_puHnadS#U9kzA+1t3J4TfT+Vp~uEs~QqkeCHe;ioU^YV@`jV3(LBhX*A@Pmk+f{h_A96(Gke{AM86P)yKg7|mHZj*e;U3!Egz(sqwG7m=u7);Ud_udLb=G7d`EUnE)@4+H=`!&SS`EMn*Mhe>9J49eEEDBGB+leJ0BX2leR-uqK$hVn{_IhLZPQP9FYcQQJa@Qd-j&?N~Yb%iYgJTx$0aa%a#6?Qwk;)|S3)bt8TYNRuy00ii$pUON_5EpHCJkzP)U3t<oE7@}?Ru<jQT^kq312kK|BNys>kHXwk@6LN4*GB|)wP(y^LhuLE!IFY_M+`cmO-^9={4bOwIIPiPHOXR@o(6-Ikr@r!~R9A0edrG2b)sEvC-ZEC{%pvjjdgaljVef{lX6RAKSjCMZKI(vq{Dwv{KQDwgt4r%}ovQKmrm2zm(MEmlR2<LJ4+LmZ<>C>M6KoFn*jl>W2(=Hv8Ys5m=3w-GmJ7m!2agoD|w$l_`JWd?CodZk%A)huh2_4XY&|llM%s>C|sPo@_sGu{R@}4Nz<G!I5M0R0+ba%~IUHMOo}+#RT?LjfW7$P$aBwIm&Yac`>KMBFU<lPAp%XFY;`VYycLBunOS4;;`WP0w8^hL+}6mx1WAlRD7DP-bBxbhtc!5F+LJ!Cj^9VE>Evc{`3Cm>?{c4;}1ACnL)hB^Ead3!8q`2Z2sGR{9Ahe^8^0;=|Oyb5xfoF{uaT>^FQFEfR9t9@v2-dzzQ@i+*tPyA^dMVKFR?FB32hwH+gyOd(RP_XZw55LQ<^ZP^%j7_VBaDIKlUZhw+h26=E-tC?U=Rlq6#OQiD6dXdo+=++dtzqzD{I8AkNo*Ln#Y+UV~HpbOI%j@CoN5iQ#HgvdP$iI7GFi3{j8S>}xo(@++{2*PCFnJqN{C-7J>I5T*RG{H#M<2GSE>;=&hd_<+98nh(xY=UkJjR?)_QBQvY-IFv4njWD{YlxwF>dVEzR-M;=K<8JFv`BDx79HrXV<(6pHXaYDXiYM7gAn^A=49F*iKR@-WnrSMMTJtFdhhWmyT=_%V8Mff%f-=-(dePQ`6>usO(N$*pg4g*qd{*CA%_*Ak*AXmPn~wkz+g+%JgLCADlhi;-oloFo%35Hw$A6cDZ8G+UNYpIedU<rBkt(9T;{11d{x`&ETa_Z-q83<)pkYV1hdVn7wCi8etZyE0qtE@q~%>a$4DLACb%=?e#$F|I(aCELzXAkJP)*}>MxkzG<eEsV2B+A^hlf#F#JpBus$YGH0b$32r;<d-+5K~h#P%(?@`}<Pdq)S%mGY{W02XvZiLzD80-`hP?ZZ_kC5QlmdK<kYdivKDUF=p^4!{jA%lGi4h(A)T#1Ct+loXeJf`p?DQ>vm3+Yr^2ey+WLX;`3M;U|yuxg@(VuL4Rl#iy$1_S0*88YTkjHD*Lk(1ttNgIApQt9%9byMQ$JI@MVa2Ep4FF?I3WY}J1U{6Y}b1p-OJO>G8eMB@y4v3ZD5ym$r{-P#sFfWL8@jk5*i5}BugeQ>=v@nU3FJ!YP9|r6t^9fEru_I0NP;PwN2wX1=x#tWYA_6gnTPzD&MuwP-K~c#nR%R6^Q#io3+7_Lbkc2~Qyv5KeZJ{x42Oy8YXls_`jx0QT5Yl6mwDIUkmTj%8Mc2Xy;^={t=(7|>*OrB&LQ`nKn<>H%8Z}EAJPv@be)Q7~xi}?8k&VYq4`nx*^%MiQm?5Y5YxhEgDGbysV7IHJ1o|axup$X4b<5Km9$QvzjC#Y`F-ViP?#b$*49q5Q*k)Lgt6RRJnSdVCwie|&rJh_4wkiu#8#gh5@CL|jczVK(ggeS-kiTN#6=v=sSvlEjrACXgZXU<ns<$cBFn1ennQqzE(^^uOGed20kz1k)=_7ELT-QFXL#Z<qPl!Bf+VwgX8uUxsi*6V;88W*-+pKFF(9Xst`odrhm05(Sh>%3qs`40Qj~$n=_fYI4@mZ3xyqqLCTVR1dS=J`(x|ee5-#oC-SjE04DbZJ@qqvJRd6F28B(NZ)!i8ocH(tx&oS?w~wQR@44umxZf}#4DV3o?8@5sumH=@C@350M6&Nq9=B#sIvoWwZa^)C=VvYi#yN25CszYvBM*QFWQ0NobcYx4c*$EhW4!x=2`4IR&I&R+Z%IIHlE!{RGhwfIsgu(AZ{rnEtbn8&h*uv2Ts!_zI&?>xr9-nYYP%jq?}I9X-2@-YYc+kx_du52hH+nN}-#TSV?V1F5i?+5Zbl?NIbxJ+^ojIa67EL2UI4|;s>XK*m`WAD(3_htvOq=FYMiw+dHir4BZmN*0igvbkB?@k-X%!SX@g%Y^}Wz^OYL8&JNc9?oq7^3)F?i^kM5sjkhk<dxQAoEyi89$Py=>ut7YdWToKHRoP%|UJ*T8>(uc(9}0kReXvbh3F4SyByY8C!03=s-GP*9kC|6<nKlau@51MG@}8xgl?)d{pM1psXZS&&#qw9~zr-?1soYuYeqZ;~}(x=?&{ymgKSpXW?y`-*VsPn2Kj*!E1j5S@3e>QOM?H$_E~N<@Q00MJis@i41u*abe&WK+ptM`4aEBI%~Lwrxre&VQJ){xDAtdirW}Vj2RsuThwc?@7D3DM5N%=I+4gUE53n*ecuNJ-V>5GdazYYvJ90yds$X!8bi`LaF+Z6WC0JK6Al~lRTtzd11VttYVcyPqNgSh4yt^nrfc-g%WqL{Xl7iYn)9SyR&Y{fEitN#c?~di#1OIJj8|C2Q5Nx6_#Y$WzF`lX9o~`a8x!jeUULmFc%QCDB7+Rep)NAyXO`W}%+)-jtgE|&Ag#)U!6yFs$#uDGh@ylltL%nWrn$sO(w3_voR@cPY;87Pxx^JUR|XZIiwn^}uk~28PnfQZ{4QD7=p}1RoHkdtsn|$6nU0*r&LU#rgkg*7OfRJft1FvrT^Mcd8>O94r%mQmv0S>r6W9rjT1AYdUQE5PwZpR=Od6~7PN~Bk?MmD_ebs+&-6|9OLkoWoNWOjHCK;7yyI3qn8fMwxOl&tT{f@A>xHhm@0_hoFGc>j)`cJ^MEbwcRN9=u)<=`C9SAcs+$x?wk%r_>>s)qO~Ejis&Ds-8+nE|q*ktoM|@-@U5`DBUAq^g%lu@<Q%aVo@_n`%pn#Mq9pSlk8;fM2kRZjh;1N{kmpLaLquTswAz(kCm5zu9`UdP7&2mSsM{iljs|N1Y1pmXw&gr&R7<W4EAxVf(F2w<Fisyjq3q!|3$BW%8;;;6d9|!Eb=;TYL9!-!+a-@lH{0c~M?Cp<igQNsZ6?+i`3ty>2QWrkD_v*UKC?(!D|Az0-My*LSW5ym$KEr*8$f*3^^Lf?7}FD^m1luWy24Gk5|D#UT+UThlRjmhlb&=xCirC{QuaF?esyJ)>~_BOE{T<(wd`Sl)&Ji)}$9t2VNsI{h?BY11_N8PyXX>)hcOa|eWLWfK<CVK1%$0%I}nL~a&mxmIXogP3*eXv~qhg6r+apLjLlg%G^u!!^jOE|6FIkD)V8GX5kFWS2zO{8vw%@6wlDl(EL+>Y<C|@IVMw>~>U;@UgMNg`@f?aP-^)Tsn}?d;F&^)=2jNh+8u>kL76*U(#-aanEzOG(r%&!{yl;#{D4%VTaf#Fn)-KxU*@v<;k`bbn&q1b}m%v#Z#r5#<GJ5isVlsgfYQaEC$2nKS1N@i}@x(miD$SX&3k|?*0H5%1}E|?3%b?%DYb%8`V*<jz99I$|mkUu|TdU2?y5)(FCwU!b))uT<JXhN?wEZH;W1mCOvp;g$9JsjuqsT2d4li&c2Yj<p=f2y1+?p!I>uu>UPG4U~1n8nYu8L^J!`*v8wtRM&J!9QeK&4H;{a-sKm0!o2Xu1Bf{E02w4vsAa3gHcRuiY@a1R7)9M){y{iGrPeuwjd9*){h>w-JXu%uh0hL2ro$Fp}TC$p9t;iQ#jyBG@v4afz*F~JWiRaeALr2?~{G~!L?6oHWQ}LQceebhuLiaxhUhrCj<opJ27VL>Vvsf+ZBwx&u!G2_@T|#{LLDaZ5N*URdDLyw1Y)mz(4N^o#;)Ww@!s*JpjakiVi?#bmHp1;Wt^0erdeI^5UD#iBV}JFP*juXHJf>QTH5n6~T^(ZHILpRc+Bu)Vgha571u&QPiqOth($3JEF@ISbE9<$2sD$ZQVy|t9Uoy%?Fa_tHy;2hqk2~*Bz0GWluQ1@YQi`c}kN9P>a4%8+uz5!OFcw!Ca9TJiO^4Jh_iy!95MM62Ws%&1FUNibi8Ro@3jEW8y53!86@w;E_+k9O{-et~8elWJGAjUh&%xRf(ZeP;O?ESD;yhWG%LW_yi9>9)%5J1fP8Zogy(a(w=wijS2Y}ekLsiz8MtJH5uXUqB#_^39_nN!AlUFvbb$9E6T5t?)o_f>}YhV6W7pMRrmxpbJ%<84TdqOXLfcf9lB1_A{t7GiXf0s>~U+IBA4Fz4yOzVMQ0i+|qBl;!UsQuPcTrYLT+wBZX&}jvyly6H-wCYjk<zrGaeg4r6Z+uTTRtjqk9=$OajgVF+_h#1Vc6PTDPaLJ^*5+;ZPy&T*y_BA`bBHlU;B-A>xb)<~y_nbQbd!gG0!%&Fk%t6ZT&8Amq!}5z-Qcvg4vgbq3v1neqs3xJ$od2pE#Y)=*@0AZDJ>05*4~QZ4mrfcNQdo#eCuIH^Qiq&N74+eK5{YB5@skG%3p)i?t-&d6xzM#4$o?>_pVetuuv=(%&@)A>x0{)<@9eJs6!Kb7Vxtd+NBm#Y4LPfMM%Mul@vEk6k+%+uOPs3GF$8VH7R^puxP4<4-v+lojC;|Qx%nNWZBo@lC_s^T*>Nda48%h7JOYPjo<OxOr!t~+PX;*ZJ4IzbkK9Lzv;=IJK2w?o(~2+LpW$t7UdsBUlxDxx>QXMwkgETuU(ZiRp53Z4hBwc{fIae2{-F~oP?e6K9NLM0^3dKQIyKKcB9O`2BS2w-59~to{X`L=_v-+RF1t#8f|lvkDp>~Z6++MlUVGD4{p1Q_DrqBRy<$ZiLD0JEr=K@Q%r=>-{=SFg&>&I%~tS~e35+Si2#87vBEZXu4LSoc}CT!%VUkZ&;;#}x`oP!0N-Lv3OedWyDXQo6BN8q!6U$}m%JS2#F%1nB{>t#)YzIMBpG0wm8H#0yxi&*2im0pQxrG|+27RRc-_Mm!BupfRQ_s@ki9$zm2g9WUgt}4IqkbAl{j4=Sjb*zWI}v+E)~nUZ{!_pLx1_+ykJk+g~`k)=MuSuH#}r&#+mb;zI_=-iQDy>(Sgb2f$5@KcjtIc{&&=RlfNc(lP+|tCYyYz{gs6Tp9qyn&0*ju-f<uNxF51vGJup2vJy$zO&ZZWK%{6O*F7icjpV!X9Nz~35R9<m8AnWhKp7Im{Jt7k`N0eInFQ|#sGjz`w%1VqR%^_s2L68dBWMmkPs*}N>w#BJr?5;$lEJr2aXDB`;P@)<Equ8yo2Hz5?s}|OOn=W7{xx<f8QW+@`uMsX7?Fsc+Oa*Z0Od^O9B_!e!&alNEh+E(<<S$QvJY$grWFTnn%v*c3FwTL^JgpS&sEl+nJ{BVhi_W>bMbz4=bQHe1_<KY>H`J*5;A=ICAxd{_Z_05yaUY7{`GABERCOic=qwx)r0q1B8cYr{=FYKub|u6z?N=Hx*wzkPG$$Odi!tVHAmYX)vH~^;_uW^Y>+=hQRD@?de!k#52(_MSk9~ViWO>*3}rLr28>{`W&N!JH5Db%Q9r5ORCk(+_RKEK(Q<)m@bBgAYR>7pwlX}67DIRFKo7MtcYPn$aS!&;VqmkTE)@O}_Le1f9Sa;48{tp1kWDGO%JA(s!go~RtWuN~q<fD)_}bFkx0LAGrMfM=*mXPRZ)MC?#Si0$V0#0Cy$hH+nFmiQ=DlW{<-5)IB6c(`xV;Ep-GRXVEDNRvnl2MojS~;s<fTwCycxsy*6sn^kpj9eRThxu`PwQ)$_;>Sa);FeM>w;lSC?hwvQ;j@HV?N*XBu=BOuK5>=0L`0b^Lcqu64;mk~JBERr=V-CN}XsVL!J~JAN7Ei}!jtZ#YO;5NN+pD*rYNEH|cRoH(Y+d>rE`7RRbvkp7f{X!W^T*A1VatTKGz6i>R~sSP)pyYr*7e_fqkv7^6VTn<m(Y4yK0f9WV9;256MU!iGJ?g7#N2d>COW&',
    'T6': 'c-qZedvn`3lK($d`3^kJ<%ZnIjHSJ#W>t}=vKf0Pb(th(yE3(<=vcHw+1yZ~ilqD)ufF?rHy#8CP@*2m-0YSki9n;#-Dq?-z~2v!>$)GO(=?gKPBf3whaxGQX_h;eublHU=X4%jrLi;4X0vEMIZC54o{t}#yXZECMl<K_#b;-lq;U~EeewhR-+7xY9-Q(f#{O@A?E=jS{M)M<I@3IxQH!h?Cus_QvqcO)EN55FBu{Q*Cz+R-lg(qKH_g&XoOhgjIfpjDTQSa)McE<3MV!Xt5`K?L-q5+biRaD&hFFyGByi4P$ayq_VWXVrEutikC(acyndCIHZ0<~AMmK|@aQ5+iG+U%`AG_dxK@fy*AI53i4bRWQj~_yqTll(6fN}{fPl9oAyQXc=(iWRvf*2N2UI61!T4YW%9><HaaM)bV-(8%(ID7N{y~3Up^m$Sw^J|A|L^68Wbp|B(oIQVe{LC5WS%F^y*{Da2dd{y&dKKs8Q9QqmQ&a=*)vfnBi_&!Ma8uskZ_qQ1ff~p$$%~RmWm926py6!Mf{#t)z%(VL_uME4Nbd+(qF=$TxunS8!f?7Qmw6n9PBL3$c?kwI&&sGwvU$N9@&{a%{2MbH{-w;LaeNhxze>6j7#vkAT8Qtm!6GVe(&S1!{{&y<v+^sLIw+byyZqXrH8M$}G`rTBnnmRz%}SsYEFSQa6BW)PEtMwo<qXyy{5)UC5nzce9)cpdhNUgq-7sNu`hS0Oar*w<htu%v{LS0TcOO519FK$UOVawg4{zSv9-MyoeER<5r_=D_w~rq$e|`7iAK?W&?gq#4(JOKT=N~UF@aIpb=NIoTE>AzaJ*_nX>&6DSkhBqA9xG4fKspoD1XqTY!r!#Vb`q@3r}rN(!(TqVL&j)aE|f_O7JHLrMI0vc#j*^Uckz7Z=!8@#7eJnYD!}r-dcOKoaF>)flRUbserC=Je9u5pau5|?C*YCBu(Th%GAYw|;9fAd#y#3No0m~C$JGEnm3d3)O<jFd@DpX8Jye4xQwOYEjxE`y9#K-n&iQhV_IjG<S?;+XGKVHC-6@!p=o~16#juv<BneME_I2^)aWn~u+YlC%hs&T(9Y}`d61?khz6j=%D9@vZj#GUeNiJaxp92{uaxUYqFtK@Z?-UE9T-?NQNn!v`o@QfM#~`0&LAw>s=f!Q7meDnCUFmW*F9PNdxTx+5&_bFNr3btPND!2ZEC#^u+;ejm7>e(3R~)$T8T?B&fe{AovYa0MbOZ|ue?^%U12>NsDfk7~_v>=Ukv7SE>Yl=8^?;M<JCDQ!?pQ0A3zYiZ5Q|`pllUH3|G;-g{0}KvA8-j+pWz-yzju&F_y|*6&f*-rmxoLb``zQ?k+1PShYdeUlRwCWhvPU+1F*Ja;rT(D-O=hmviJZA0-2H8o%kHX9+04qL<I~J#z)7wjnZYTb5V`vqcmOY?qU<x7W&CfkM2Ea!!2T1DwCr6=&m#dggkcV0Vy={@PGqj)R2_RNn9Hnde`TVyP?UFCUJ}A>EC)A%>-)W43D~_di%O%m~7a3UBNo}s@P;fAyUz&g*0R<dYWbt1ZVWmXoT_|DN90|3&e7`M9?a=NuxTrO_C*Qot``0{>YF~nLil6DI5mT0#^0J^N209?_njek<Zzs-!;jMlm#vRi|;W)bG*-}cv`?6=N&et&%Tun^x(;SDHmUy7W$M~e1&mEpKm)9Ag2)oJmp>HkU=J6;mg?89!KLFlu)oncm_LylByWBud;ZpQ)!a4LR(0i2^9&NQdA^a39^y!A}WgdGq5Iz>^(lF@0<*rUMm~mJ%&fUQOA*Ax+7n8x12if>pQQV-tlHX{x(~t6Ef;)g4xM(I!(q&JTI$!iBlwxQMvj+^Y!FY&^N)B7!|Z73QjuCq<mP!1Cp(jDvXwtG3$^N?3>4^4Er4i{J8U94bNcS+CC!3=1o`!q*2>d%qEq?Naka36_iL2PhkIJjM34hdc(_n8INecpCr?%rwj{PbdLQ7hLmr^Y5*Hs?31mL@WPA?gvEcvEmixwP#}x-nd{==hE11hl%x#V5!~sq5+<dgu#v)Jv6m7pDGnBP+>S$k@G1K{(jul$K#XVKFEF=aS@)9ELHXzl{CjqW+m}7VR6-?~7bGZi5NjTT9%l<6=4sM$|JVEZ(`yLIrpf(D@cicxze1v0#wRq|zu9R2#-aX=L;1d|6Mz67`;2cOBa73MVYfG`H}QV%3%Za%f0;b~v<l(xs}(-@GI{R*?E61|!NmGM-Hz5Z4&MFa!^iW}w~*$@CsIS#dA3|YSnN!aami#$pCBz0qJ^nHu0(qls$<Hic7Z>7v*jGpwebxSCe`r<mCMEQ0>qXWNF6IHA3ZQy5}Fh<f*`+6vn$X23(n2ew->PHX#N0UYhFSi27e1oIz0FCRrrQiazfYVL?Q13qGYD{r7(A@`YxsM`5Y1?^#RQfxaal}(05O+0>8#5t`=%yj4DMX9JHNAjY0L%*BDcOq7slkJ(pRdt5M+QMIlt_Y0dc~SKp_|hU=8+N7oOQz-;bm?WJu%9~Z6N#hEiI6d=-az#x$xt|$y}TwD@vZAw2l{}aaxiUA-Cft-#OF_N2r6G+gRstXp>1XhNFU=MCMDm{?LZIUg~jdF_*ins*N6D`xy0|9oxc>UlyhW(n!qOL$m0bCzOY!0_nG`tcLfT9d%<Qh+8)A}Pr^_-O<j6WLkaYxLiZmfbWs429NzbJA;A4&ne+Lg_%Mt~JrieXoN>W%z*5JerC>jb$LSq>SlCxi$49k^+~E~GB4Tk3<seNTQB?M5oUIocs4e`1L}ip924s;ZR@i}vS`+FZ{m3wy*Eh4bXE(C3e%1s1t<Z*Gt{%a(KAo1_MP(Y2>>8LJxyJ<~%IzLe>SzG+rm)l@(Nbss^VtG-x-h4aWIO0)D=t}PbbS_PvG+jxBttai}q=Cu_uaT%>Kn}XC9R~5)Ca12I4e+yRDVP$5eT0`r-<FNg&Lwjup*A4BrS2#j!p@-$H7g#d345n!x;v1nfMcsYpQPBmA_TwCu>yIldwNqB*uADGg9!&)3$X|zj*pgLR8mW4?Lqe;S(s(J^(?TIumF$4i%H!e&yq2<3aZ`nDisfO0ZpQ(43;P)sdgG7>!-H<MiOG5fB`?wZItCYi?{zggz8I4tKYa%jUaRAbmwApc`+zqdGP*|&`U~|Z&D>~;Wc|`rzOH8GydFrp=*6q80`>}4b9dQ(AbX!Ey-#Fs%f8A-Znd8mWwyXAErHyol0?T5$u*amMcDdElMPCqrlhH-il&bPjgW6S34g=Ho2k53saOP1G1E#0&`YYg(w5PcTQkdI_2h$B4UAIEG>=38;8n^yl4JPJ3&#^<D?jUwbltFIg`g)n(q9}4Z_vu#)30-F&2_*lLeB(t2&t}>T*6LNI*)R4Rpu&|I-Mc@5r^mnJY_kLZ@M84!angV2*32?&%H38R*MqV#QU&B?LP*xHTwaUJgT1=&N$7A*i%Olg!3{>2i^GSrw;JWK6YQ~i=U0n6KFh<Sy(A?7VAAJ?ip0vj<bbQfuG<KVd@0xGpzg1J(2v1K6>RCx`54Z%u3x$9C#9ykw{3f96K)42PFenw*R@xBuY>`xQdDxOWolZHo1~QUT_1)9g39E#*8x}U#&evX=6wI0IBMQY+%_SiBgMn6bl*lb>>Do4H|s|g(HQ<zFy|Zr{N!Qo`thGV!|OaI8w<GG5mQAC-T&i`NFHh<40_sIzZLFm}OacBRs1R8<Sd&afW9-UwZ+Dp&Lq#(23L4Y)0n`d?ASBHul8NRD*#Hz3_B8=%(~vI?gPc!~++;#;!W;HA)ntte0D(24#1P`t&Vm9NQ$=Z~$4!P+f1@u0+{c%{{=Pk^X$3S?_b}IVQN}$~=$M4t`BR8evtU&zp}xfqEsnBej=Cy##@jU0LLpL0F?JR%Y3eyR$v?Qhw9N>~)+_A53`9%Ivght0So~pWwXx_(oaXrZ*<E=k~*6t~Q~9qpHV7wbki1YONwJyB$(%{Nlv-P+K+B&U&k*4v#8N?k{3lx_u=v(3+t3-Z6oOpWVK?(9DKs<VZv_lDygIQcO@btfs6rzU+ntv;cK!Kc8}OX>2KaZ)jcdCat2OrOu$P6P>{xW3X47B)f<c@>?^!4Tg-}^$nNU(&J5iFz0UkO7C$&N`{$31O*W!k)D*bHng#T*@Ub$wyAA4TKi9m5mh}DGm_0$m96R_X#wech*xH)w%F0`dTk6#EAs~-FVj*<Ig@LMDDP<lU!-MFEU$2o!h^Nc#lpok##jT2hknA;^Crq+ec<tjTzlAkcsvS-JHZPVt<F&zUB&4DO|-g}=NfdP`y1S8J;v+Mu;`IS2vU(E%WYI{H7aE4Bf%CYaM?9G7I7Iq&~wlI%nxL|&%W6(jjyrRtN-4Wa_DJts8k?%@1YR*r7`*3HOSS)VO>O(OvGcmV7eB;^lXCZnH^Cu_gpX(wR@2E*_#RZNoGs|c1;0-wSfUH=mHU>aF3OZuc#tK{QhVz=^sb^;IGq_vs7*pA+0jGzA3{rddQY#Z4@kyCD&ptEs}w_L{)`muo_rC1`TWZ;a#4T^&3GQc2>>KO7K`^M5kWb&46hVr;|cEL|J#%&FhCvN7)ec;Zw|+s78ylt*6KAkvN-xK5~+H*5W8vhVEKi8S?RV9#%B9u}CC2?!Lus@C!`;clv>9qaDTpmE;DD8Q<(1&_C>=gxB~Sg+!NoDwW6rU6hWOc(*f@8*K=B&S3$wE{1Zduj<+3TS3Pu;zfjaXmFuI@PqYm{B(9Eu8`8n2oOoe+JPgl@8WS9kHkUa9ezxLpI2?vUtPbVLk(A+hkeGv259Fjf?%=T8JK#m0|~Y#iB|Cm@aOv0L!ngOO_kj&`)a+aRSgM~6B0oY3UW<%XXNz(nNjf`zGVd%Hv;UpSHv-mWNIXq1M8&T2wK9_@+3Cbsb1IxJ41`c*?AsQGaif4`55e!9@94e>Ff+-t8r$#AYpQ8zbnDC%&HZbEh<+5^wU2v6*1XIqf!sdUC><K-eAEQ0~tBBj?sE^G)v9iuvMo=U3xu&UzVVPDw_%wP|+AndcT2wXUka=y<}T*(fvAdkcDqW4&;ZIJ}2QX@ohqPv3Zzj^~GYCqU5%R_hBa4bkJ3YJJC<7WzvRRc*ntU)e^G(ZpEFkM0}H9avwYn4c{UWOcL;4t?jCQCL*oL5}9&88}gM}MXF+Rl%3b_ka+bYrMte|^K!yX9vGJ?ZDL>qFF<q7Rixkjpg@F{vP&e`f<y|gT-#!k&=;e20EicRqd7_)FWA`(d?ZelwImz$(%7Tn&YLP{b|h%`0yDNMwqV<`jyAAu>#;A}Z1ru!wz*z6v2E$GC);#8nDGp}g=H4bmorbA&XH7y>_I!J(L+xENZOsc``mM}u;ag;UrW^LX_`#Xe(E@{s=6Ys0%#iD2k=SrL-!q1(DljaK4YAvSw0xLWpvfQ0)K@o2tQ}UB4i$F<A*rU2SD`}c9HQ{Z%9(>Fj0(ZbfX2a{>r@31bkx_!4d)Y@|E+Z$qVo}M7%!Z)=7wbrFsjD-DMfG-LBqT<Sk-6@xqS?S?U=N8w0|>uZ4GS{hj$AkNxA<u1SG%_?2yWgZ+<W;i2|A(mE91X<VJ6=w~jrQ#14dO2kT(E^Z<ZUwkn(2;vNH`+Ruu#;vcWep746&S*%Hczv1A2amY)el&NNA4mGt2oMi{Z7KGw^o=bGC4zBhc<=enb4Pr;OD5%whn4<baNLnU-^7^rdA_;z-9MD^eL3);@k{e5J{xCQJ}CxnHl2dpoJ9uVRpA;<Z=!)aj<G~!KD&iyS6NwRGyR#S{BMz+(r!G9AR3~5HBpX|`HL_E`};tm6)=Kt%?w2I7ocYK9^RvPFD#g6cb?yW_RnXtXOr;Rug`vac2Vh=kqKt_PLTRS(VZotR;pOFb5=HaETf3agw_kD^$QT}auBin2cGn*MT8&-^en}85!_l#qpPFZt3ZN9^MYUPq}A57XYa}?93Az@Yu}S-`w)=ip(@REfCc$dVi@ID8nQNKHR+Tk4tXV(rq(^Zb#GnkTEg3Ru<U4WdKM)qz8HBPM+IdkuDLK*hBT_8DmQ(!c-3M=01+(ch+w~>YkhQKtd7{dwk~ohZy=n~KbUPtc$IS8w`(3t68kH?T^jiP?Sl#;Ee!<egPcs#t7bjDk?)pA`0`^PuRD%cx<1g)iFDhd_^Na$O%8pm(DOO<DCnXzt0?-my2Qp(SFP6rjtKp<F-1zk-tUHJigSVW0vx|if?n9&f(EV`S!k5gRv1zvo9cb6rfgDWqt;Wu8|r?UL&z9eLpV%8l_Iev4XB4$8}q%s-CnH8KiCk9l4@la2$A{@1YqX<M%K++8(sNEFMneow=0tk40366E=_S&-mB9@0`e!`-Ap>$bdT;ucO&_0(Y+`qyt}oW(}F_#09ezI@Gck3y!jin$|_m8jcsu$D2?%}rM2{0;;JolVT*WWyym04J`T1YQW-D|=}Ji$Vr&_P)wPm(_{6SvT|5+JJUhKl@C5}7MytCGEw(Dj^oKX^|9SE5!g=%ekLQ=CzgT^(L^24dTL>-m%C*5!m${3lPY#C|^7P4J;X)1z7P2)|hyee1d3^kP3$&1R%`Jc-PoM077D6+p+IYdkm;xuXpo$EnX_TdrCJa207HA^7V2PMWBKzQo912Cmh9N?wP%x?%@F7p1sMsM2a)^l=vH>-u88f5}Vu&2D87{;I7P1*C<mr=UoR9{TkPR3i8xca*;e%{M2U&{^vKbkqjtheSYl8`5Ac9!&K)xv&hz$$Gjs&t74#*l55FxDn5J0{O{>L{*|1hyXc0~SQ9j&k*4WJ**kRRIte(VMJ@h5|Q{CQ9xy8wM`hWXeI<YO0z4-3EtcTjE7JvL)|Y=!J$qD_1gRFCa2J(>|clq~U3Xdca29?eJ|>v25nC>~p5c(g(AXvXhof!?tXc84@X8*s-rgzeytw>@UZArL!S;dN|8>o^Ej#}-H(EpR$EpmZD(qhljNhlpdgMd#pN(gxD87NP_92}<vHdbUWH1v|EVf#<cdTv>%O4d@)}u{ntN$(PA<@8|xPfPrWH9=d8bN=D&HJ1mclNFF=mcr>AS@G8)D2p+BQJ4mW1Agux0@RTNN06t83k17D$gza%KWRK>(%7W_A9@C=%(W5n<hn=nJ_q9+STLO7(2jg)V5D&TGp#_C^@)D*#BYfyqTh`719S4W!Xb;ZO3YtTchHSuPBWAC_cIcoTO0TvE9y@?Xv_tRU;1ZnU*1#Qm!*=Wi+96DXV|CO|FSH}RPg*^&U}zU~VxcyB?x5iZ|AIE2yv1_8a(;J7<T63x(gt!K(_+B(R*<+Vb2VsFQAL_CMYcs0*%41fK@(YvCDMo_GJ`dn9I-vTHL6HUOcB0&^Lp{?`3b8S6P6c4^{RkH?64wQ(b{QMsx}zh7l-6vC?tw0nFu7ZX!i}#NA|!T*$;W-yW@^DqK>SOHTFXs`Tu}7vJP!z{oEUoMs~s(*&k)(dt;0o4q-$r<pZFLd>?F)U6DoF;EHUADq>)Y90XCM0Z(LiG?87fMD|A#*$+pATll^pBHKZT90EXO9el_)2M^g3I%E&vkORPmRO`u#8S;$~L%t<m$oEGJ`E#*CECu_+;)Hxdl#o9GBjis;2+`vD$BLcCd$cW~L0SNVYyk^m1qIm#5~M94$hL4GRxps3P$1g^foz5W*$xC`D+mw+07OKL2M7PyANu1kz#oTz{n!!oqb1~r9q{9za36;S``8icqZQCcTbK_!$j9LzKAK*kYV`t@?e!_K(6$HmFkn5_A9@>*9xVVp+Q4}n0?cD~D3A3(9-Cl1lp>dWtwB_Aw*l}l;5%@GMj2rZ!FY4DLhfL-M7+8n?~=;HN&RF_yFh}+WgW&rb(ch(t;s5MA3qM|*&CLVef2cWZYI^)i$vU@>UQ|MTRSu14Xbw%)>m`thdTVbdekI152DG$^1MAN$$Cz9KqT23kHm^b!k5>hgzTVmuN78H6l9WC(riL0*@RDGIipgK%7oOFVlM2z!;xRdEaA3R11ymTwZb;k!wjZdzvhw-fNWx*x7e{wNL`H+v|TgV40xj5wy{8-7%zi~wbU0f>R1|WKt^f(Qk?nP+P>&02E~2xQA~n+BBU4u*I=aBE?aGbl49%M1}CN7$G7pl7AvLxa;Y6FMGNBh$4OC9QjASay!0mFsW^;E`hW%LhCQx2F&zj;@mO6ul#6BLTi=&k)vsoYjc?=K?8{fRS2*aE6_`Vmq;%hjoEXfr6a`yGGR?BD%LRLrRUWrn8Sb#R_o|gS^85Ic^?IVBGX5GUz05uj#hdGF+#z#b;_`$)s(dX`5p&^=IGauz36L9IM}U{f!Fc%DGg9IqnBO<fLUcG4?M9>qs6zea`-^N*RPtj_ee>v9300H>=DN$4ViUF~ZtZzolpU)F^eb_h9k~qG_ne0ufyFkW#X~A1njNbV*Yt!DnV+<dXu42!M5CiJqO~XMtHZ<4Em66r%QRq8-q1@Re|@E8`i<BznBl+jt6A02V<dcrbHu5?aMT)%jv`a3UiGNducO9rlzehbrt0@PfQ-$K&5?kyO8<37ZgdjDKXUB6j?^y#rsk!7We@e$ET`vf<W!k@OF==XwX39*Wp$*Q{03PmmU`qgFuX}xRX|$DwIRQVL8juzx%qVz)pUdtRtNHg0y-*R9qDgH+j^u+kUO&V8qgggVkYPkaYsnPlAv2rcj}F5xI6XerQh6R>OS(p+oSKObaeb3>nN5`Xfp<n9f7A#N5SCvZ`<>=C_MGXIu1|$sgA^B#F&uIk*)qzdnj$TJq(q2^YZG!VYkpOxL2;KVOmv9zp7Si)E0!w4nfrbKs5xbp%_EC>t=~LDgtKR?%>t7I#y8|q@si16xmRUY8XY8*HVk^X5P9rSkYEcMa}QJY!6ekGf2@k5Jl!P+x~)!u^QI`7;S-Mvs)ct4T8-!_%*H3jap(G*^*Ba*T^K-3hBtA#&v>}inTuynzlXdHFcCHDCQ$>glp}@c0*XAn{aKDD^#y<8JC4w5AJve?an6}*=mNrXu@gw=C<9sX7$|`fq-z@3c#rb-?S}w(^}}JeSn+H`s}6?4qxrEEoxKv_lHmf9vs1G5Bw&L8pCpGiR82^j+6SXzy=T}6aS+x4+ia^PW4x5s4`Tq@#^nDw}F0Y?Q<wR=Zg}&1#>;bOa=Y43-D8uvoUjs-PyCUwk71#UVu+~!9DE@_Ow0JlSL=(V4k)CdD;TvNe6f$gq1bmPG-Q_4D8fyUuyyCv;op-EuhoWC;tbTWXjY',
    'T7': 'c-p-?Yj4{~lHUX5KlE`g7jh#pmJZ3z2$2JiWA7N3u?^cuuuyaaO|~q;6iJXRTZ*CoepS^EHYv*T?BaF?h^6kT>gwuxcgt~{i`gtqGS0#*OxIOXu~}ZSo4>In6TD3G=)Rv9JY!d<teP)HoIhs$G!#6G*6cBS-~hc~$JgK3EJ=Cgzkc=l)p1^|nV55=a{T)MD97-3SVOT{nJ)+;uc9PP;a^w-29}Fk7MIBb$JKL|XZ-c6S)Rtc?6Gp00R-?@MP*Wm9+9nh$|C{ep<oh_J<fsK0v0F)k9~Fu3ufU077a_FR)k5(V|GgnCMApV#UjsG%q7(v7Q+4WRk$cpK6?F%!GFH*2Oka&z6ZzaDCL9TOp7MC0MP`n$KaDlU=0CShkjH&yby1=jraQ14alVk%L?{#SLVy2Vo_L#WeHmVw&5I#{1N7G`w`M5nS-eH@i28fius!X8`Ck=2a20U^jVm~(ciHT2X9zZ=2Zn>e1$b6tRee7NpE>6`aFB!DV~x>5_8~a4l-w<ptGDMWhK}EXyr3xGcNN2sC`w+GDezxU<bw=$3dn^4T4~{#N7n}OBO|53fNhei%=waRw0xcCF!Woc#`U)D8q=~hS9wu7sGNWQ%y{NSJOo(=4o=PXD{Kani2OTAU$()-y>y>lQ7NiEIt;YDAHU25x-dDCl*$$NQD8&mJ85-7|aSq8kD?P`&DuWCZ_4nrCgElZ%5ZBU(e1@g43&`<D0XKbCA`6Klt$a)#=&!(bw(alk;yUUoS3Cg6lsnE^dB5JO6ud4U+@^fcHOw{asyLU*q8A$<_7Q_07ro@ks;V>WrpQZyU5f9GE$?M00TfI6eCMwG;bSYqA?1AuhjO+yq}P&yXEl5C4z(j79T2uXvDT#Zm;)lwFVY59x@MDwjD(8}#Vj_38otF%k2)3?KEN^a)_(W9tC@u)2@I^dm6wwJQ>l^09L*y@zrfQJ#q~$xye^n#d&?m*v<ig0~Q5zSavSGX@H4)>X_BbeL3}T`e=zz>~7fOV>Hi8Ew+|E6@~;>YHUam_$Z<clD%K3#;T|91w>An1zc<JtAO|m{@}Anq-Ba#bH^7>mI8=PZf7yfmc9;h3tmk!)CH%#i|17R&&k;Z5+G}xC<~ukjSzMZ&Ps5u#fi-c`Cv?4gyP;i>&g=nx$|aE0B7cRKf+md?W|Qi0t_oL|mtIfEoUX0cY&MXYdSp4D`p&Qq20l^ub1O6eU%Sost(RShwSOO-bWA&r5mcoPhsd<2FX@i5S2juyII;h%_hOK;L4%0-nz)*5CvZ=@?d<?wG-NL8Con3_ilHmJ42j<8hJG$!KtJF!d~kGl-dCn*1akokToMeNd>Ra6Lao-wuk8L~#Pi0g-8qO#DUQz7uq9DDO!kdFZi+FkSK{2lZMWiqAoG2SFq(;2ESRX9dD{Dh`$qSM^6{V=b@c@XvFA6n1j3#$_?~CSn=$#>#-(-a7`dZH6pKcbqi+Z9+7TytT%pKbSV5+nTV=z&6e;n8LkQy^3;+YD9)Ik=8m(^AK_x`ZJxPNT){S8ykQG2u%Rh&)O$SUnw7{Tm$7D8;qtcp@?$b8nHO@!vd@|c3olzm3UH%XzD38X}lqfsWDXjA74eh5X}9CCrqZ<V`m6TpS=zi&<iKoQrQ+yD|?%NVW*INV6-2pfI*o@8babB9&<)Imqgk#$vl&X(Hy1X?-W;y4Nav!1RKw?$`1Cs<!VvYvXvr4Vr`jOa;wF*gJ=;}RdWXP1rlCYt>&@AF&n<%q!s+6Kb-cM`7)S##$hVL3ejVq*zjPV6F$zDkRPJH&JxVlm$O+CB|H;#>7eM5_1AvduN^fx2XrzJ#KMY#m}a8KVzDmxm}IG>2n!{GOYo$U-&{#!GU_pK#O%Lz%)xH05Mb6-01_N2Gy=jlfVTZ4i@@qJr<UA+W=EI{qfc}vH|3H~DKf{&Z01@^!kMuHuagz)+oUGH&5-=G4WeDBBmo)ePyV8F{H{++X4cXLsg$7O;H(VBTCS)0s`D7E#saMhl`AtME0eJtBm_w1cX~80|D9U-%q0+0CFgsrTx6+<A%dzY@)WA_fxxTls5*OrO+fyif&5AihqWjG-?aqe{IC1@@)P7pvt)JXzxy1}SEO<HBYx+89{up)ulL#e9ym4{6@Lgz)f&QjtxC{qz(ZzD&~vDRczQS)45v*1Sc=w`W#f<d>6c9a|9;%ygk-_{`~%C$|8#m5BKOb!etvOvat!r`8drx!btYu4EKVXJMNOZeBGPgOu{>>*pm5KX4J^Z!1+vVbOp4}6lFksI?4X9_HKc%;{v2pKN8gHigbF1U!7uO9{ML2;id%8)U;%m@W@|_+O97b^{99oW;5s*d3yxG9BXB%L5{fM#Nh*q8s&>?AMw|wF@1RJjhiOZ|vQiYEzPm>Ey#{}1Aq3D`%}7SO-Y)!Zw(dDr(S+{PiMfu{M#n-`YD0;Ra2vpC%CQ3u5O_%PB{~<S1b)Q@*k-s)g$pb8;7mRLjzg@H=*?{!d~gbwlQ4CPcq{_)*-8WpavFyQv^~`lkYZy3YfmR?&8d|7+~S*bkbtnxqhQ*U3<5ZH_(E4hscKC_ZqK45EB<OSFrS7~ubDLviu5d?R25Y6Ja)C@!2N?4^O_PGkU|?@G&El&l(LAT#N&dpMXTPER>F<dUDF4xp|2o6zssnQc*5YU81Xjf2UXX|>TPWkIL()t!X*I#CVIY<3vRnonls2syJ^sl95pi_=9%*Zvf7NaLRRcaNsG4PZJd|ObNXy}YN`*`*Q5xlj@Mf-35Qe!ICBmR!0vr^vMOZ5K=uip7Qr;RhbXE1{-5jv(vi0b%0N_>yego&LOyl4^4qYDBZhFU&&E1asBbOFgGk?IV25uv9kfnlSF`p(q*}CVpkhX$U>#0vN*1AJbSGI_Jsj0TbQ@7$_Pu&_!w`cDc4Cq|gIRRnvV0g1z#738Az<(<;(^NY#slrKO{GowcbMIAu=$lcXi)L=ij>~i&R#p99*dSB{S13a5_BS|_8D5fJ!EMfZH{!?_`FY66ZF&APzC!i8)V}#bIJsz;!rhHV;@@BhX#8qh^Te<2CYiu1%`(Na`bxtJ*I0wC8-VpH{yYw7AoUTQ`Z*A2$z{6zlM2l&xUtX`D9bs+IY>%-n|2cInw%kT4+jVWvpms(yFX&wFYKosJ6u1`l$MbzQ^7(SFdXg+b;3BB}w1lWXw8$WgUD0R5zyA<hV0&2WJR)3AlK3KpSJG*tmZ(8`CwHG-bLC`pE;daESba0I7oEz$cZ(<Cmcs9tP!1+ZvvR!jL7#e~b;R8~{7U6PB70rBKJab_O%B$jVQp$kXwF_kZaD)AA!W!pS3zY*ge!6@TcQ3%84!Vz-G$S<1NPJjx5|@Jgsym^1@uFR-hexw?4MICh^B5J0{6R94OnE*gg-)I}Bc0V9#Ft?JRl-OOeW<tT@5!-``=HHaXb3Ca+`(hqu+0HRWD-7k1`tsJI1(VCYJbtZ)@V5ysgX@^WK4VjFZtWEX02t#Y{!S7oPjvCmtIgT6ryrWwTG^Bcsz71h^QO<&&yv&0I52aj5bNV(v#0?Jb+(;Qd2IgW+`petORaZN%y@bZ77I`k_+Vg6uOQDyTj<}tdJMTfCZ0%~1Ib;J%(V85Aw*+}U@K&B=v)*g<zX~7Xe&se7L2uU_X`wChn2#Np<95~QbvE)W7HvjaMhC;sXqraac{+%`OU(>&w~qaVo!YmVp}F<EI>j5utV+<8j@Ykq9y2<{XkX-#B&!P0tdcLnB&+<IBU^Rt4ZfrMHB{Z2@4gM8Q};T6cd5^g%Ri$)s;vY#VqjX>CUNWb!VRBq8DEb%N-p+^ccpOFShMpWhCLSS;z0Xrqo+*)b}w%ylzNN3mowGKZ=Y(i&KDg(oe^{Jp3`2`IeCC20B6q5Z%(ehUL5~V05*KyfWi9UOrB-%%^BHlsSS2YN~wWJ8QLH>S_}rP7K{aYGYBe}hXc2{RRV3WW2ssvY|WEV&`!7}W6cw@85>H*hW21xQC(4WRPjK)A5g%U4Gmt1u|yPC;do$K1OBlgMcEA8w?Z3iAK7bCFIgNy@<n80(SRT~3VItzScKT=TAfQvb!+%-<=xbVQs;3Ml)VAIQlLIlukp}jZ(3K|9h~1X6{ZeL?jR3aQT!@W;aAIBT%vNpm<L$S-(hw#rflLDtjFeI3D$@$Z)Ns~JUN*9#F^$piL%K}8s74B>@*&*=r*p?q86^^7zSL)?PSYLrVVnCxmS6)P2-_KgY0LjSySjYgEm^lMX;t>*ZHOz@$NUT4VLmd?D^TF10#iDLkhL<4bMXiKk7k_nRi;m+Cu2G%UTk8x=S2`4si^-#4&86p*U7b9F*g_P#1LpX_*-|+Cm$&g$CLM3Rln*4l7#BoTpm1HzP|~O63>yyC}Oa0$6HiZ=ZD}Z>J;wzh@f;w%-U}$c#wt<|0VLb-ol0Cb8p`+-burlCggKq9Zw&jx2rv%&NT}%S5yvcJ$;$rR=6-<9$jOB-S<XEa7QfSxxrcmz=8c)_sc!*(J_lF+!b6q}p+TC?|9i8!*(Suo2I!7axx=c=6%kYbor1S?iDxO3VzZ2mcFf_&W_y_pm2dr^eM-GUBx{eflHs!QCCRC>DC6p@grxSYaA1QC9+L4F_<HC782>+&Y2!Ykd=StSPXH7a=}^z)qxAKWu*kQWD9hmUKOBUiGCM?4Z7rYF^B!>n~ceekq{uTy~##{cMIB`Hs0G`G&m#IF!>6()0cIB2pfYn6ztBmo)U<zJW;1gFgfHe2{M4D5yoO3k6M44ejMqz9-R1zrt=cnA$hMUiUSGeH$XlDgGd=O{SjKe@VhT@K{24DihweMun<g$y5Oz<TGvWQENx7OK^)w*G0G#jV$f4Jh1M3EeOM^-pNy|S*;IvWVmku97*#C_9bG?!dhK*du?iez>zGLi$6gvd4^cH`jf<<n%YI|T7>w9E)Kr%hlF0Iwva2K^Z?%_#QAJI@TfH^4*^Pa*$zGM589;33{`Fy93wbp^!V3;+%#M7=HObnp7K_K()v30({aQgJ{L^%lSTu(_f4a0GlH9g<E@rZ_Ch^GZ?0MR5GE--6+j!1{Ml_S$^a^LXM}Hx-YT7gpnJDr-yPet8IF`$Y3JNDjR}%@p=M6I$t&g-QXf0%zf4#6(3dLOP5(ve`#k-XA}nu9=mC!gsk?xb&9`%Tlg0nqV;?o_ZAh1>;mU_kR`vxIwmu0<W!Jnc$FTYXguLk9otVSw8@cvs3G@w#scn2wSP&l{n~eit0rN+h20Ea3>a>}W7SqyV`U$#xK7T21h8tvbu6n%nKBNVx^HsgtGihcs<(11o)W5IXRmZ5b0$2X=>X-p?Ht$M*CWVV9U>UI;B!HZHI^S;gX~TMvMbx?Ok1QKY#BC4tcDiv|ehhiTcpt1>kG*61+hY=ox$C`$;!qFGIp)mnmXlqr1z(A=3w7fM2(b7i%JVX=#!fz)fvn(6K;rl|bsmnLh~qvSjHhGgHWwmabhIA-UvhNp>dE)%iMgLr54y+QZ`mabtdDQM#~S!OoQ?fz<w^fu!O!wX*Bib0$D75QIC%5>n?K%MZycM5zrYvm^;7sqoo>|1V^{ko`M~m8?iWi={p6<eDOU6LR6ZFXR)1nI)GoMu-?#I)?Prjk%?5gF)p*LTnQK3WY2Dp)Ar89`Wp&rH8_tcZt&Rt%FT3n&B6hCoFAF@)c1k6?iAHqT9jcG119V>oW;G2{j-4b?)?)029X4TyyJ1_U!iZrLrwi~Df;|3;=L*8V?2)$J!=@#qx;3>;PMOL3R$2dy<MNAU8(9DMtZdC$cTefV@0uO%LfmnwVkzUZsw2&;quzFy724XtX4HP{@h>$uI#^Q=l27Ol-c>`z4r8M(y~mpL-=Ol(Hst?IWB%W7L{x9T$cm6Q$hl2+)1@@1B1h&|oFV@9X@u{{qK)mEF;M7YPqlSh-4XJq#sV=sJ)=HXM53Z6nzBjNSq4AbRI%<gI|>dtwnnaiP=l`*<TJmm1JWj9aGNOofbU<Rie9Hnd=}Gowq`M~vsklP>&BJG?fl3f?G5)Oq9{zq>-_CQm$di!psosShxbvejsL0Kv)4@%>IOycMb+1Sdf`gbdr7smmtG}eMX$48uowUNgnOrZ)(+rW>W=!|o27&e7tn@AO|!u9tATG_+$+_y3x4zKWO`HCtoF&+`_$Al)t&X*jz&(2UQ0i41a72BZd$)5P`?-jR}lo*{|y3qa25oZkOzUISZ(H?vSo3-t^{A4tP*@FjM=;1;oi_@*gQY_`j6|gYj*V8#nsKpmk#f0eva%DQULRI_j!wx0m<V30EVq2#Q',
    'T8': 'c-p-@e{<Woj{kS2-+`xhy-Ka4#?EzT*RRe_woP|4*QT8$m%g3Ur%^0A(W@+JB{_*Vm+yW6_(P;bJIP+}GM!o$2@nJU5CB1Yp7&`!&(fT;lONgJ{eQ5mn0?!dXMZfyI&IP-XIb3ve70h79%rjMt=V<jTo%iQ#q75<JLgrynkvrgc~LFGr%#?f*}p0Itl<e_ypFFpHn4e`@p}C934^~d45K$?am}mUFVS8*)SG-a0usclibrpM8%A%MbiwN;UX;7xtiF0If}K8sr%&GTd72Y|qUdDECO~3UH>~6pOcJKtunN%T7i?A)H9Zt__S5z={AeyYTkyDERxnK<6FIk)=H;>x_~<X5HR)B_tU~IczA@k+(337=WD)Wb7+Dorh9ew(JeKGUJm+!EafP-jz9_B`NHR5Khedx4aDZjalTc1=<eJ(%76Y1d!$|?h1f_f{E3Qc(K74xnAFPVg%$!<7oS-qKl<+H_4Uj20qC;F_(4pX1+SDwmijp<ObzCKEo1IDmKS=qSAgNoh6eL7Pyk2Gi6IYzg;%v6eh+j1?;|iX#6@$4l7_?e3k_2dK$|B91kR9Y#ah4_kS2nOh_0uQ7xj4-k{6oSL-oSKl<&IB5tP>i9-^{XQ0x#!Pv0%XAD!$+~(BgUC(<k&Yisnm17)2~ylm#$7&hw&)Ns^Fg`If{?K37~n0=H)TJf3}%=n|L*3a3J*zRTt^ZZ5O*T(y3Nud=222GfKY%a)UG15&RfjkDsypaVp#%!&q(gyjmKSX{F*YjlTvxhPloGA|MAZ){KL_^ikq5Xw4aC&aLMOsi2ZQ`GycfF%a)uK28gwXB!3OT;qT-e!yXRgzu-+faG;-kuzs9-RCheK|OM_vuS?42wJpxB1o%v>$ysK0Y}7HTt}NbbN4pvVZt?pE?X>hhO%N_dgsQ?nm#B5YMMWXxk2l+fSdoKRDd`V80&0>yu%0a<q4Ne1LE5=AW>6w6+<MemppgKJ1_Fe*mkoclhgmbV^Ozub)2o{Nd9{^zQQk68Cx=Ira1ju!PMni=yTc>3uXWvV>QDz_xa2t)ykC3Sc{k_BTIhUxcXkNflqKm!ROnWKN8g4CDG+0%mO16xGUawXaE=j8D8{f?;A@S}+jtR4hzAD@sn{O?KB4z$OI=*r2LdDFV_t14-6&jN8#b^J~tImN|;%epMBf?}6E&NvpvXSm>!CIOs`EeDGCc&@!&#aS{=Y5$KAKl0K%+#Jy%&X8bHK!#s(rDqam(`#jZby{>^-8JIJJGk`2a+^`OYv4TGwA%x-b&U^gxDsRw#oAYb5xf9RT2==N-fYlRk+03_o+^W+HXE)?lJ@G1DW}y1rE<yV>uog%z2F`KwLd?DWMG0F9EcBS&(j@bE!S6icH3D4{HzGg<+&V0BxvwCKXjWv)MQ**nDzXO5o~sGXEuaBuPx#FMAJLq0Fd-ZbvX9iBjfdOY)4(7)L(@!heKzA+7J|^Fr5}X!9+U;KVgup=3{z9efkd!ivrCREeP;8HsIXrlDoGre|0TD{lY|5vG;`+RKpu7$P=G~)-+DLRfO$2Txjd=cNAIphW00)4_y0jPj<uHR6_N@bPnI^YrLCbBuvk3Cjj%I=u|2LOOj;J~KDK0;1DtIQr*_|-v~`B>L!5(le^atu;|zgFvI1wqYRt1DZeTmakLeUwV5)V>ARl%()G(B8V~$e9r7YHG2Ea=;WaDXvA5FCy_qAS<6~-mlo5W#o@Xsd-QEAQ$j!gqeIyKnrnFc{0Rp%A>FuZJ-e~O|&CSm~25UdeB2M%I!+eq`J)N!8G8~D)yCm~H|29HfRL#Z9Zx;bZHRooNYKxD4z%lWp`U4yiY>$;~x8SDmfw1Dk`V{-pms{UtNqv?R@FGE@zEoX>W0tW02ctx9yc!zi1+hUm|B%TnZpmQ~!r?V8Db{$Mf?4<0i)ek*uLy;C8d4r}1_RzSdJya1lU`ewo`Gj~Zxl4f&7v0lZeDej4v+;m|acBRwrVY4YY%Y3S*+Fzi4AxH5@1RsZ&1azO6q^t=AgMEq6woaCXD8K?PsuSz()rvshJ>|b+kul5<J(z_zE6;N>UU8VQ=kaz^iTeP^__cP)Vc|8U)Y{y9iL~sw}iF>Wehh^bhU0&8K+gGgrfF^GiEqahFyexSDKaq3<>{XC-MJOnKd8-q6)Ob6$f@NN^l(w>8!l}@_+vPrmP@(zS#|5{v6R)io(ApFa4j#Uqko|eh$7wo(X0I=m=rt77#8lBlf@o1Wz0RW|t6V@oe{OxHGjoz?ejY2=Bipw?E!R@c$2Y*dS;Le*TJK|G%2f7fkQq*TYXo`)?t{7+9{?MO7?I@PAp7&KkirdeS_jP32bZs0#=rA!>`w?$$m8U>3kK7h78*H!XlLHz|G-FTm+UcWGNGFd9=75R??L!eMoh73aS90w?B~RviR1&R5`1R}FYt@PCbghVPyH7^%oE@&btx;y*x1vWH*l?)G4B4HkJXArNX`)0BW@DL)~7_q9+57JVrC)S)w)=8UqnE_gRt-#8`@2t1_q^F1M?o&hR_2s*-=_*Q4x<e57RAh=44C0Y$B5@F3783+3?;sDk(2roEnP6FJXriKS?M=yiPRm5d!!2hkAXhGIsS9k3;br>YcID@gLXL8J`;QFPp7E&Uq8?<(J7urs3R-ppJ+y`A(wbRc~Tf4X3YS0{YoL<2xi>R~hY4^==DiRpA@w77yY)6DJQ5%YFT~rWK`f6=p=Kup3*lRE(l_3c9ZFlrn(QB&du5hvtODXZbRf2xkhS1tFtKLTt30&l`1wen?@g*t29md}>^TZ&^o<YC^0em09`(l|(SmGQ&qS4KGBXY674PRk4j@mSEDy)8Od1-(rqGd$xOU~S4H{hpK_Ouep4xo3Im?JIr%6p%!bXqM27&FiA_L5<c5&jeUz+`S|{PD#d`{Icq2?^P(mb`w%D=k8IPO$YVR`h}>7z||LZmd+gX18+Av{1pF*Y`#rDLm6hIBb{<Aw`j?7qxEMSrcl3ah%{q$aI1n&0F1Nte2(VID5UlZKbw{_hrUO#xc9uMK>#|iq|ECtw<q~hKvZi{Va(#_-}~tnB5u;z}DwEn6~G44qT{9(5*G=q)9Yiz@p!tjfbPDiOK6pi1dVri$(@XB+Mi9d+PK8Hn%Y|Ge$Z#1PLZzsBM&D=6T**j%ie&%}l>jMxH0&36^m}EJMcTjzoJ%nPDSLpk3(3AJV?BLx5J&bvM{PO`lQB5FPY$HJd)rGd4m%)7ex_?RI<_XLGaxvf<S$Hd3}ZNyx8&QQ%pQGGIPJ3<wTl&qa~&iHDO{_6GC$QT@jfGvvw*2S4=6bPOf#7xKZ_8lXQcYwZp=*egY+0U;=$^u1?$W-XWMKdQ!uPvCXBST1~n#sOw*10&N&iSn|b!-Gg2cKE&Ez!Dz0rhTcBV3(jym*9zwr9&-Vi%czO>X@M)D3PEOCekD%it@ESh?()olvHRoGN6VJIqsqB$XeT=<jpaL4ddvbV0BW8BKSDJ;N}Pe5#b6s?YN(s3n;6eI6(8djwLPw38TPqCWhLA&=0~X&0hA#?qC?@PM+dEq`L;v-L**NRwNOvb%qV~Yl;$C3LBuokb&gfOV<hnk7kx}W+!6!Ec49?jFE~dPD{@**vO~{B7EaUsP=1(<N<t2IK-`;g_*+2^cKqSX{uN<1{IokC6K^(npD@ZZ5MbWUNky{$p{RjQHaPL?<NVf483e=deIxa3xczA<LPa0hG~<zE|VT5O^Tfxy^%S(BF6IGYWccr&v|2b-&(Nn2k_WV+v6c+HtDSYPaff}D@T}J4v8d(MZ_iuxL_*<QFpfQtOwnRu;QT8WLQCrtX(HS>0FZ}GBN)$(F2JWV8l>Xg^bU#qBb`z1v@Zb?Q~yNO_5E8eCtQ!T`b=XU#ksjY~#TB$P60oKE58S9YS{u`s1@nJF{6)(ixrHV^C*>nBe}0=Y+Yha$pK3Fgy+bcw_qvuH(ED92CPbm7Bs-npu;rj3ivxo+$}i7R|t!ZA@B`>7s|KSkQfF+u1h#BH8qU^^SRiHWe9X55Zs`2P<$@Z{gnjD)hvC_Zc;qh$gsy8*O<y9GH=dM(#e~7T5=d$|-SFTyw0krD11do4@*G0DH}pPdl;I60DCoMhl?pQYMyDZQ>gAnAznqmD9x88p>6-wS;RtwC49=*)k+!+{b}oA?0y9ShsrQYBlM?g3k2I0UnvfH%op5+Jv32SdoKT-F60)cKGkT*o{hZ_W!ZfE@QdG-B0Ri0+I;}1~K9#CX*e}Qv<rGx5i0-!Cta8RTD3|Oi}m-h*P<so80TQR<>PA<F!es@mhIB1Glz0w6m*OXk<6ar?A6oWs~67%cX>N%Ot^4)zieLW6MC>o*U`j-$nj*2q9H&QRP)>3ZNcqt>{6->na2@O1`dRnHy4dRiYfoA%%Zq@6B2;s=>l}QZ<v}NmB@u51T!YEB5c<Yj#SJNX+mFnz!KtgHu^J){HFKwipa@Jz68zzFD21*LKT-w<6DGG+@z#gnh^5QXpPa&_`}Tmlb)QtrH^PdYr)Pdb$Sd>6&3HV6x>6d+d>9TY#r<patR=F$xItZ`IouY{++nNG<BXc_3L)VIQ7}7?TGhRC{BzF6PUTcsVj(VixQJu9Perq%Ly!jr|rzM?vSt?#piXAFS@+IQ*jQ#i=*8_SSC4J!MD_gm4i+*AlM<dQXPM0ugG`0YE#h<SIIijy@g}L*2q=jqs5-mqh}-oe8nOJ9A&&T5MlHWQ%KBmYxgt0(>7Ea7Q!cKXX(R&@7y)sf%3z!H^G~&JLdJL}&wAo{%iL`GL&ShAnz8BRv3c?~WZQeFwJ80vg>1bSymB17tVEh{P?MHJ?pIhgO##YOwRpN|Fg38(4d78{F0L6>M_igd)PSP+y<_$}&8a($cA4GpDsVZFYYw*pl?{c(64b;;2=Hh(-aAC^hOF^9w~+@x%o<vNz<vmsu0m%X6H3?e7f0r@&?mU?%<$%>7Rgc!N(>@tX-&8ffE%;Ah*@khmsC9N2RnfYmtX*~B{*HJG9}T+~adtg1p2-&~==@Ws^5gc+Pl>!k3U47*@8Z=w~o`s8P-(gpuC=yuKc1s<l_6BxQCpnWnmE@Czl<En!-#1JQONs}=18DctX%+l15b2%0klWT8TYiK6}+z$A7t_^Ljne*wfZMcSQqdwc@?%rMUoz`Che?8k;mev5H%}ndrEeFFae~SD(bf{bm8#x#@axrYwr4bo+BN;|10w4BtSEp7Mch<_{sLxc;I8efp%9*V+)@^He?%03pTPb=w_N>lwR?i(f{`2WL+?k^<Vpl-p$W3~2*+f~qDwd6JyhrN@pHzP^U`d%yRQb2c2!QPn*(B&@<jk(CwCUC}4@BXdDE2_ZHSL<MdCIe-c9(;!ufLOLn(hKS@bs_^14KIUrUCl3ZK6SyTL6TdA02?T1=HTOH!!QAoyE@qmjC0iqA?EfqryByeHFfgxqqP-bV@rAqtkidL=X~mAw&8QHJJXIR%8oR9vkbRZ;M3g2s=`@Ieh18v|q=rA|QDTL%rI%QdQBTkIBdQC_7-Xo3~Q6b{WMXIn~lqDTz3GkG^=FViIwdUU%zN-Q_R}sfbnSn}JT#8^%e}Yi%pf1wFcI=v17@vG;LS^ZP4>+89KmayjJo$Yy9Ei*v=<t5I$!CMA0YfxAY=EY+bI4nU;>yQEa3&{@spVi8xVDBCs^P!}@DssYVFb8uIcr6LAr-%{Z!x)eMVc}~n98uf+d$|(ZfmBqJ?n+-~w#$pzZD+=D8P>skPFRiIwHqsz*F3Al7O3BkS=!B-dI$%{SyNLJ*i@R7Z{wfjaccDR?CjV6$;+yY4!*LU%&znT=_*F{R${sd)-6X!M5>1j~J{h{IrPZkkk+y4A@L4t0bh;)_-K11b*sz^T@W9EC$%tg6^A-_doQ$8H-goP2KD5MlVi)m#C1MwIv(*5bc3gGUN%y_?t`lCU($!E{2@zk#X-4I{WFdfzMjer8AC2q?x+lf;*>kCg;E%KwQgTbsbV!?{_A1+$v@~|Ap)gE}^PU5Mqi)pgsoGCEHwbW*?+5NzSaoM(6jK??ZP-5BRb2=R@I7X|$(Y<b>y5zQf7Zh0e3w~oFkko2dK2(>owazlkrNiBB~(12aJ8p@6s>n-bseSuG+;ldzUMJjKE^j8JQ?h}u}hJFcTrPB`EubaB{L1zidWSHx?R;xJp0x$xo3_Wf=-NVOUts$D%wDL{kNElqE0EI*))qw;=>QNK;h~D4+aXIzNhzRTKdQ)-BKIYvQxuD{uWTF=G67F01t(IaaLZ&uu}|QceVtwf3P6l;j2AwV=;>A33gHrrUJIFc|H-gv%il#pPDtEu<Y_0HuoLaJN!g0pi36r_yK##)VJ$2X)b*%4;c;g%S(<iqTj2HzF9T=rkVKL1NOSxhAp$AsFHf(74tdpiHVJ)d;9Y;o_OMbwcBz9E$6VM6jqj12h+rR5j1<SGf6uwG7k9L4Mx$ZzFU93VxL8ww}nMT_jbi^r<0EEH~lz-of;6xn{@WA_HP_-#Rln%kN?Y<9B=)LE7_?oRg!m>C+_Rpq7RIZ*PAD*^DB^0^Ag3ax~m`N#kC)dpZ)%9@hpj+{r2qRvtuQk0!g^QOWf`4Z#w{$!A7gSZR%qnopxsMTcqj)8!-E%E`~U70-1WNiq2^Ue_~3{OtOSwXvSjw+j(XMi&<1K>0ael<aIA?^y*$*pgS&5S1uckeZNA<NwB-A&DOfwch-aUj`JM1Fa^covP}9WX-%X?4)Z&`Ab1+k%E6lM_Xl?(z%%Y`j4fGO1AtlE_i4sGO8U1ey1xI8-G66u|4vR}%rL{guJe0vwDFkYh{rWez<D44ZsU5aJMS?6q{6@IWFLDTQCQ~AtNt68>whJT&T0w6op?x%nFCU))kem-Zik4ZEB<26L}y+x0snle4u8e|J?It`2*8t(0XBLtnA~P2k4pABW+g8S@6!kFUvZ7^#x4c6kEs8GmOd!`@(AsGjAlMYEBhKrSw2_Ko;o6KU|_I5q&IgT*8afEoPL34jCZYO#_v_AJyPYk#8Py#zqDUymBpR8ClB)XgXoH+$ih1_efK4c0~2J0;{uR1^4d80msLapKqS~`=(l(tjR#GSOwns57=)P)y(-k~CQHmOc7OA3n)fva$1*k?%e8*rx%~B|vDZ&_yD}^a>EuyK1}xE&fbJ}KD`2jC%a}nR!u)))9eDQ?$8WHBZp@OdNjH93W5^&jlwr+=OhaqQ)?tZn6qfao`@LA|CXra2dB=O<9~z(;Hdw9GJL4C)zy@eVcz%y6KfmkVqC6H7EfE>&u(l`4zR?xG_WByXgC%aeX(I1BH}vJtL4kxu5gunn5nbkpA`CsENL}Z!U22hMJYLleU+mwcc+(c+ltA4Azi*S$WFGE)`2G0cnC<=Y>F8wtoncN}Uta#`SV%w-w3~-%JxYeq_&<rO0nq',
}

# T5-only waveform-smoothing window. This is applied to the existing T5
# moving-average envelope path only when the operator selects smoothing.
T5_WAVEFORM_SMOOTHING_WINDOW_S = 2.0e-6


TEST_DESCRIPTIONS = {
    "T5": "Analog setpoint latency and RF rise/fall time",
    "T6": "RF Enable ON/OFF latency by power setpoint",
    "T7": "Interlock-open RF shutdown latency by power",
    "T8": "CEX lock-acquisition latency by power",
}


class FolderOverrideTransformer(ast.NodeTransformer):
    """Redirect one embedded workflow's input and output parent folders."""

    def __init__(self) -> None:
        self.in_main = False

    def visit_If(self, node: ast.If):
        # Remove each standalone script's `if __name__ == "__main__"` runner.
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
        ):
            return None
        return self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        previous = self.in_main
        self.in_main = node.name == "main"
        node = self.generic_visit(node)
        self.in_main = previous
        return node

    def visit_Assign(self, node: ast.Assign):
        node = self.generic_visit(node)
        if not self.in_main or len(node.targets) != 1:
            return node
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            return node

        if target.id == "folder":
            # Preserve the variable expected by the original workflow while
            # bypassing its own folder-selection dialog.
            node.value = ast.Name(id="_selected_input_folder", ctx=ast.Load())

        elif target.id == "output" and isinstance(node.value, ast.BinOp):
            # Preserve the original timestamped folder name expression, replacing
            # only its parent from the input folder to the selected save folder.
            node.value.left = ast.Name(
                id="_selected_output_folder",
                ctx=ast.Load(),
            )
        return node


def decode_source(test_name: str) -> str:
    packed = base64.b85decode(ANALYSIS_SOURCES[test_name].encode("ascii"))
    return zlib.decompress(packed).decode("utf-8-sig")


def load_analysis_module(
    test_name: str,
    input_folder: Path,
    output_parent: Path,
    t5_use_smoothing: bool = False,
) -> types.ModuleType:
    source = decode_source(test_name)
    tree = ast.parse(source, filename=f"embedded_{test_name}_analysis.py")
    tree = FolderOverrideTransformer().visit(tree)
    ast.fix_missing_locations(tree)

    module = types.ModuleType(f"embedded_{test_name.lower()}_analysis")
    module.__file__ = str(Path(__file__).resolve())
    module.__dict__.update(
        {
            "__name__": module.__name__,
            "_selected_input_folder": input_folder,
            "_selected_output_folder": output_parent,
        }
    )
    code = compile(tree, f"embedded_{test_name}_analysis.py", "exec")
    exec(code, module.__dict__)
    if not callable(module.__dict__.get("main")):
        raise RuntimeError(f"Embedded {test_name} analysis has no callable main().")

    # Only T5 receives a runtime smoothing override. T6, T7, and T8 are loaded
    # and executed exactly as embedded in this AnalogPortTimingAnalysis1 build.
    if test_name == "T5":
        module.ENVELOPE_SMOOTHING_S = (
            T5_WAVEFORM_SMOOTHING_WINDOW_S if t5_use_smoothing else 0.0
        )
        smoothing_mode = "enabled" if t5_use_smoothing else "disabled"
        print(f"T5 waveform smoothing: {smoothing_mode}")
        if t5_use_smoothing:
            print(
                "T5 smoothing window: "
                f"{T5_WAVEFORM_SMOOTHING_WINDOW_S * 1e6:.3f} us"
            )
    return module


def choose_test(root: Tk) -> str:
    choices = "\n".join(
        f"{index}. {name}: {TEST_DESCRIPTIONS[name]}"
        for index, name in enumerate(TEST_DESCRIPTIONS, start=1)
    )
    answer = simpledialog.askstring(
        "Select analysis test",
        "Choose the analysis to run by entering T5, T6, T7, T8, or 1-4:\n\n"
        + choices,
        parent=root,
    )
    if answer is None:
        raise RuntimeError("Analysis selection was canceled.")
    normalized = answer.strip().upper()
    numeric = {str(index): name for index, name in enumerate(TEST_DESCRIPTIONS, 1)}
    normalized = numeric.get(normalized, normalized)
    if normalized not in TEST_DESCRIPTIONS:
        raise RuntimeError(
            f"Invalid analysis selection {answer!r}. Choose T5, T6, T7, or T8."
        )
    return normalized


def choose_folder(root: Tk, title: str) -> Path:
    selected = filedialog.askdirectory(title=title, parent=root)
    if not selected:
        raise RuntimeError(f"Folder selection was canceled: {title}")
    return Path(selected)


def run_selected_analysis() -> int:
    root = Tk()
    root.withdraw()
    try:
        test_name = choose_test(root)
        t5_use_smoothing = False
        if test_name == "T5":
            t5_use_smoothing = messagebox.askyesno(
                "T5 waveform smoothing",
                "Would you like to apply waveform smoothing during T5 analysis?\n\n"
                "Yes: use the configured moving-average smoothing window.\n"
                "No: use the original unsmoothed T5 analysis.",
                parent=root,
            )
        input_folder = choose_folder(
            root,
            f"Select the folder containing {test_name} waveform CSV files",
        )
        output_parent = choose_folder(
            root,
            f"Select where {test_name} analysis results should be saved",
        )

        print("\nUnified Test Data Analysis")
        print(f"Selected test: {test_name} - {TEST_DESCRIPTIONS[test_name]}")
        print(f"Waveform input folder: {input_folder}")
        print(f"Results parent folder: {output_parent}")

        module = load_analysis_module(
            test_name,
            input_folder,
            output_parent,
            t5_use_smoothing=t5_use_smoothing,
        )
        result = module.main()
        if result not in (None, 0):
            raise RuntimeError(
                f"{test_name} analysis returned unexpected status {result!r}."
            )

        messagebox.showinfo(
            "Analysis complete",
            f"{test_name} analysis completed successfully.\n\n"
            f"Results were saved under:\n{output_parent}",
            parent=root,
        )
        return 0
    finally:
        root.destroy()


if __name__ == "__main__":
    try:
        raise SystemExit(run_selected_analysis())
    except Exception as exc:
        print(f"ANALYSIS ABORTED: {exc}")
        traceback.print_exc()
        try:
            root = Tk()
            root.withdraw()
            messagebox.showerror("Analysis aborted", str(exc), parent=root)
            root.destroy()
        except Exception:
            pass
        raise SystemExit(1)
