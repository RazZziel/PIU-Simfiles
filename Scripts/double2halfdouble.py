#!/usr/bin/env python3
"""
Fix a doubles chart so it's halfdouble.
"""
#import dp2routine
from dp2routine import parse_ssc, doubleToHalfdouble
import sys
from os import path, rename

# def RepresentsInt(s):
#     try: 
#         int(s)
#         return True
#     except ValueError:
#         return False

# with open(sys.argv[1]) as file_read:
# 	content = file_read.readlines()
# 	insideNotesBlock = False
# 	currentMeasure = 0
# 	for line in content:
# 		if (line == "#NOTES:\n"): #Start of #NOTES block
# 			insideNotesBlock = True
# 		if (insideNotesBlock):
# 			if (line[0] == ";"): #End of #NOTES block
# 				insideNotesBlock = False
# 				print(";")
# 				sys.exit(0)
# 			elif line[0] == ",":
# 				currentMeasure+=1
# 				print(line, end="")
# 			elif RepresentsInt(line[0]):
# 				if line[0] != "0" or line[1] != "0" or line[8] != "0" or line[9] != "0":
# 					print("This is not a halfdouble chart! There are notes outside the halfdouble lane.")
# 					print(line)
# 				for i in range(6):
# 					print(line[i+2],end='')
# 				print("")
# 			else:
# 				print(line, end="")

if __name__=='__main__':
	header = ""
	chartStrings=[]
	chartInfo=[]
	with open(sys.argv[1], encoding="utf-8") as file_read:
		print("Reading "+sys.argv[1])
		header, chartStrings, chartInfo=parse_ssc(file_read.read(), True)

		n = 0
		for ci in chartInfo:
			if ci['is_halfdouble']:
				n+=1
		print("Found "+str(n)+" halfdouble charts that need to be converted")
		if n<1:
			sys.exit(0)
		
		for j in range(len(chartInfo)):
			if chartInfo[j]['is_halfdouble']:
				print("Converting chart "+chartInfo[j]["DESCRIPTION"])
				content = chartStrings[j].splitlines()
				converted_line = doubleToHalfdouble(content)
				if converted_line:
					#print(converted_line)

					startNotes = chartStrings[j].index("#NOTES")
					endNotes = chartStrings[j][startNotes+10:].index(";") +startNotes+11
					print(startNotes)
					print(endNotes)
					#print(chartStrings[j][:startNotes])
					#print(finalLine[:1000]+"(...)")
					#print(chartStrings[j][endNotes:])

					complete_chart_string = chartStrings[j][:startNotes].replace("pump-double",'pump-halfdouble') + "#NOTES:\n"+converted_line+chartStrings[j][endNotes:]
					chartStrings[j] = complete_chart_string
				else:
					print("Failed to convert "+chartInfo[j]["DESCRIPTION"])
					print(sys.argv[1])
	#sys.exit(0)
	rename(sys.argv[1],sys.argv[1]+".old")
	dir = path.dirname(sys.argv[1])
	name,ext = path.splitext(sys.argv[1])
	fName = name+"_converted.ssc"
	with open(path.join(dir,fName),'w', encoding="utf-8") as f:
		f.write(header)
		for s in chartStrings:
			f.write(s)
		print("wrote "+path.join(dir,fName))