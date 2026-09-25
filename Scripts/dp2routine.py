#!/usr/bin/env python3
import sys
from os import path, rename
from typing import Dict,List,Any, Union, Literal, Tuple, TypedDict
from typing_extensions import NotRequired
from enum import Enum

#False = Converts to Rave It Out co-op x4
#True  = Converts to infinity co-op x4
#(If chart is co-op x2, this doesn't matter)
CONVERT_FOR_INFINITY=False

"""

StepF2:
x = P1 2 (hold head)
X = P1 1 (tap note)

y = P2 2 (hold head)
Y = P2 1 (tap note)

z = P3 2 (hold head)
Z = P3 1 (tap note)

2,1 = P4 (Only in Co-op X4 charts)

3 = unchanged. Because there can only be one 3 at a time, it's probably determined by checking
which side started the hold.

-------
Infinity uses two routine tracks like normal SM but P3 & P4 are additional note types

1,2 = P1/P2

S = P3/P4 Hold head
E = P3/P4 Hold end
I = P3/P4 Tap note


"""

"""
Convert StepF2 Double Performance to Routine, starting from the #NOTES block.
Usage: python3 dp2routine file.txt > output.txt
"""

class SSCData(TypedDict):
	header:Dict[str,Any]
	steps:List[Dict[str,Any]]

class CurrentlyParsing(Enum):
	HEADER = 0
	PER_STEPS_DATA = 1
	NOTEDATA = 2


class ChartInformation(TypedDict):
	STEPSTYPE:str
	CHARTNAME:str
	DESCRIPTION:str
	CHARTSTYLE:str
	DIFFICULTY:str
	METER:int
	RADARVALUES:str
	CREDIT:str
	PATCHINFO:NotRequired[str] #Not used in any sm as far as I know but StepF2 charts have it
	
	# BPMS:List[Tuple[float,float]]
	# STOPS:List[Tuple[float,float]]
	# DELAYS:List[Tuple[float,float]]
	# WARPS:List[Tuple[float,float]]
	# FAKES:List[Tuple[float,float]]
	# #SPEEDS:List[Tuple[float,float]]
	# SPEEDS:List[Tuple[float,float,float,int]]
	BPMS:List[list]
	STOPS:List[list]
	DELAYS:List[list]
	WARPS:List[list]
	FAKES:List[list]
	SPEEDS:List[list]
	SCROLLS:List[list]
	TIMESIGNATURES:List[Tuple[float,int,int]]

	OFFSET:float
	DISPLAYBPM:Tuple[float,float]
	TICKCOUNTS:List[Tuple[float,int]]
	COMBOS:List[Tuple[float,int]]
	LABELS:List[Tuple[float,str]]
	NOTES:str

	is_sf2_co_op:bool
	is_halfdouble:bool

def represents_int(s):
	try: 
		int(s)
		return True
	except ValueError:
		return False

def parse_chart_information(chartString:str, check_for_halfdouble:bool=False)->ChartInformation:
	"""Parses the header information for a chart.

	Args:
		chartString (str): _description_

	Returns:
		ChartInformation: _description_
	"""	

	chartInfo:ChartInformation = {
		"CHARTNAME":"",
		"STEPSTYPE":"invalid",
		"DESCRIPTION":"",
		"CHARTSTYLE":"",
		"OFFSET":0.0,
		"DISPLAYBPM":(0.0,0.0),
		"METER":99,
		"DIFFICULTY":"EDIT",
		"RADARVALUES":"",
		"CREDIT":"",
		#"PATCHINFO":""
		"NOTES":"",

		"BPMS":[],
		"STOPS":[],
		"DELAYS":[],
		"WARPS":[],
		"SPEEDS":[],
		"SCROLLS":[],
		"FAKES":[],
		"TIMESIGNATURES":[],
		"TICKCOUNTS":[],
		"COMBOS":[],
		"LABELS":[],

		# A chart cannot be both, co-op takes priority over halfdouble
		"is_sf2_co_op":False,
		"is_halfdouble":False
	}

	#For ssc we have to split at ;, not \n

	line:str = ""
	lines = chartString.splitlines()
	for i in range(len(lines)):

		tmp_line=lines[i]
		#print("tmp_line "+tmp_line)
		#if tmp_line=="#NOTES:": #This is where the fun begins
		#	break
			#print("==== Parsing notes! ====")
			#sscData['steps'][curSteps]["NOTES"]=[[]]
			#curNotesMeasure=0
			#curParse=CurrentlyParsing.NOTEDATA
		if not tmp_line.startswith("//"): #This should be changed so it cuts off past //
			line += tmp_line
		#i+=1
		
		if line.endswith(";"):
			#print(line)
			#tagStr = line.substr(1,len(line)-2)

			if ":" not in line:
				print("WTF???? Garbage in chart header!!!")
				print(line)
				line=""
				break
			tagData=line[1:-1].split(":",1) #Cut off # at beginning and ; at end
			#print(tagData)
			match tagData[0]:
				#floats
				#case "SAMPLESTART" | "SAMPLELENGTH" | "LASTSECONDHINT" | "VERSION":
				#	sscData['header'][tagData[0]]=float(tagData[1])
				case "OFFSET":
					chartInfo[tagData[0]]=float(tagData[1])
				case "DISPLAYBPM": #Can be either global or per-steps
					dispBPM = [0.0,0.0]
					if ":" in tagData[1]:
						dispBPM=tagData[1].split(':')
					else:
						dispBPM=[tagData[1],tagData[1]]
						#dispBPM
					chartInfo[tagData[0]]=(float(dispBPM[0]),float(dispBPM[1]))
				case "BPMS" | "SCROLLS" | "SPEEDS" | "STOPS" | "DELAYS" | "FAKES" | "WARPS": #[float,*float,]. Speeds is [float,float,float,int] but I'm lazy.
					timingData = []
					if ',' in tagData[1]:
						bpms = tagData[1].split(",")
						for bpm in bpms:
							tmp:List[Any] = bpm.split("=")
							for i in range(len(tmp)):
								tmp[i]=float(tmp[i])
							timingData.append(tmp)
						chartInfo[tagData[0]]=timingData
				case "TICKCOUNTS" | "COMBOS": #[float,int]
					timingData_t:List[Tuple[float,int]] = []
					bpms = tagData[1].split(",")
					for bpm in bpms:
						tmp = bpm.split("=")
						timingData_t.append(
							( float(tmp[0]),int(tmp[1]) )
						)
					chartInfo[tagData[0]]=timingData_t
				case "TIMESIGNATURES": #[float,int,int]
					timingData_tSig:List[Tuple[float,int,int]] = []
					bpms = tagData[1].split(",")
					for bpm in bpms:
						tmp = bpm.split("=")
						timingData_tSig.append(
							(float(tmp[0]),int(tmp[1]),int(tmp[2]))
						)
					chartInfo[tagData[0]]=timingData_tSig
				case "LABELS": #[float,string]
					labels:List[Tuple[float,str]] = []
					bpms = tagData[1].split(",")
					for bpm in bpms:
						tmp = bpm.split("=")
						labels.append((float(tmp[0]),tmp[1]))
					chartInfo[tagData[0]]=labels
				case "METER": #In SM this is an unsigned int.
					chartInfo[tagData[0]]=int(tagData[1])
				case _: #Anything else, such as DIFFICULTY
					chartInfo[tagData[0]]=tagData[1] #type: ignore

			#clear buffer
			line=""
	if chartInfo['STEPSTYPE']=="pump-double":
		if 'X' in chartInfo['NOTES'] or 'Y' in chartInfo['NOTES'] or 'Z' in chartInfo['NOTES']:
			chartInfo['is_sf2_co_op']=True
		if check_for_halfdouble and chartInfo['is_sf2_co_op']==False:
			chartInfo['is_halfdouble'] = check_if_halfdouble(chartString,chartInfo['DESCRIPTION'])
	return chartInfo

def check_if_halfdouble(chartString:str, chartName:str="???"):
	#if chartInfo['STEPSTYPE'] != "pump-double":
	#	return False
	#print(chartInfo['NOTES'])
	#return False

	insideNotesBlock = False
	for line in chartString.splitlines():
		if line == "#NOTES:": #Start of #NOTES block
			insideNotesBlock = True
			continue
		if insideNotesBlock:
			if len(line) >= 10:
				#Check -1 and -2 because stupid things like {1|x|0|0} could make the string longer
				if line[0] != "0" or line[1] != "0" or line[-2] != "0" or line[-1] != "0":
					print(f"Found note in side tracks for {chartName}, not halfdouble.")
					#print(line)
					return False
			elif line.startswith(";"):
				break
			#else:
			#	print(line)
	if insideNotesBlock == False:
		print(f"Malformed chart {chartName}, no header.")
		return False
	return True

def parse_ssc(sscString:str, check_for_halfdouble:bool=False)->Tuple[str, List[str], List[ChartInformation]]:
	"""Parses the entire ssc and divides it up by chart.

	Args:
		sscString (str): The string containing the entire ssc.

	Returns:
		Tuple[str, List[str], List[ChartInformation]]
		Header:str: The string containing the header of the ssc. Not super useful.
		Charts:List[str]: A list of strings that contain the chart. Concatenating them together will
		provide the entire chart.
		List[ChartInformation]: A list containing a dictionary of the parsed chart information, like meter, StepsType for each chart.
	"""	
	
	header:str=""
	numCharts=0
	charts:List[str]=[]
	chartInfo:List[ChartInformation]=[]
	inHeader=True #if inside the global header, not the per-chart headers
	for line in sscString.splitlines():
		if inHeader:
			#Found the beginning of a chart, so we are no longer in the header
			if line.startswith("#NOTEDATA"):
				inHeader=False
				
				# At this point the size of charts is 0.
				# append a new string to charts, then +=1
				# (so the tracked size is now 1)
				charts.append(line+"\n")
				numCharts+=1
			else:
				header+=line+"\n"
		else:
			# Found another chart, append the line we just read to the
			# new chart string in the list and +=1 numCharts so it switches
			# to the new chart
			if line.startswith("#NOTEDATA"):
				charts.append(line+"\n")
				numCharts+=1
			else:
				#Keep appending to the current chart
				charts[numCharts-1]+=line+"\n"
	
	# "Self documenting code" is for midwits btw I'm not spending
	# five minutes reading this shit when I could look at a comment
	
	for c in charts:
		chartInfo.append(parse_chart_information(c,check_for_halfdouble))
	print("Parsed "+str(numCharts)+" charts")
	return header,charts,chartInfo


def convertForRIO(content:list[str])->str:
	#print(content)
	insideNotesBlock = False
	currentMeasure = 0
	ChartTracks = [[],[],[],[]]
	
	#Since StepF2 combines hold ends together, we have to keep track of which one started
	#the hold head so we can put the hold end in the right chart.
	HoldEnds= []
	for i in range(len(ChartTracks)):
		HoldEnds.append([False,False,False,False,False,False,False,False,False,False])
	for line in content:
			
		if (line == "#NOTES:"): #Start of #NOTES block
			insideNotesBlock = True
			continue
			#print("#START")
		if (insideNotesBlock):
			if line[0] == ",":
				currentMeasure+=1
				for chart in ChartTracks:
					chart.append(", //Measure "+ str(currentMeasure))
			elif (line[0] == ";"): #End of #NOTES block
				insideNotesBlock = False
				#print("Measures: "+str(len(noteChart)))
				#print(noteChart[0])
				
				break
				#sys.exit(0)
			else:
				#print("Current measure:"+str(currentMeasure))
				NoteLines = []
				for i in range(len(ChartTracks)):
					NoteLines.append(["0","0","0","0","0","0","0","0","0","0"])
				
				for i in range(len(line)):
					#print(str(i) + " "+ line[i])
					#Player 1
					if line[i] == "x":
						NoteLines[0][i] = "2"
						HoldEnds[0][i] = True
					elif line[i] == "X":
						NoteLines[0][i] = "1"
					#Player 2
					elif line[i] == "y":
						NoteLines[1][i] = "2"
						HoldEnds[1][i] = True
					elif line[i] == "Y":
						NoteLines[1][i] = "1"
					#Player 3
					elif line[i] == "z":
						NoteLines[2][i] = "2"
						HoldEnds[2][i] = True
					elif line[i] == "Z":
						NoteLines[2][i] = "1"
					#Player 4
					elif line[i] == "2":
						NoteLines[3][i] = "2"
						HoldEnds[3][i] = True
					elif line[i] == "1":
						NoteLines[3][i] = "1"
					#Hold ends
					elif line[i] == "3":
						for jj in range(len(HoldEnds)):
							if HoldEnds[jj][i]:
								NoteLines[jj][i] = "3"
								HoldEnds[jj][i] = False
					#Just shove it into the p1 track
					elif line[i] == "F":
						NoteLines[0][i] = "F"
					elif line[i] == "M":
						NoteLines[0][i] = "M"
					elif line[i] == "\n" or line[i] == "0":
						pass
					else:
						print("WARNING: Line contains unknown characters: "+line)

				for i in range(len(ChartTracks)):
					ChartTracks[i].append("".join(NoteLines[i]))

	
	finalLine = ""
	for i in range(len(ChartTracks)):
		if '1' not in ''.join(ChartTracks[i]):
			print("Track for player " +str(i+1) + " is empty.")
	
	for i in range(len(ChartTracks)):
		for line in ChartTracks[i]:
			finalLine+=line+"\n"
		if i < len(ChartTracks)-1:
			finalLine+="&\n"
	#finalLine+=";\n"
	return finalLine

def convertForInfinity(content:list[str])->str:
	insideNotesBlock = False
	currentMeasure = 0
	ChartTracks = [[],[]]
	#Since StepF2 combines hold ends together, we have to keep track of which one started
	#the hold head so we can put the hold end in the right chart.
	HoldEnds= []
	for i in range(4):
		HoldEnds.append([False,False,False,False,False,False,False,False,False,False])
	for line in content:
		if (line == "#NOTES:"): #Start of #NOTES block
			insideNotesBlock = True
			#print("#START")
		if (insideNotesBlock):
			if line[0] == ",":
				currentMeasure+=1
				for chart in ChartTracks:
					chart.append(", //Measure "+ str(currentMeasure))
			elif (line[0] == ";"): #End of #NOTES block
				insideNotesBlock = False
				break
			else:
				#print("Current measure:"+str(currentMeasure))
				NoteLines = []
				for i in range(len(ChartTracks)):
					NoteLines.append(["0","0","0","0","0","0","0","0","0","0"])
				
				for i in range(len(line)):
					#print(str(i) + " "+ line[i])
					#Player 1
					if line[i] == "x":
						NoteLines[0][i] = "2"
						HoldEnds[0][i] = True
					elif line[i] == "X":
						NoteLines[0][i] = "1"
					#Player 2
					elif line[i] == "y":
						NoteLines[1][i] = "2"
						HoldEnds[1][i] = True
					elif line[i] == "Y":
						NoteLines[1][i] = "1"
					#Player 3
					elif line[i] == "z":
						NoteLines[0][i] = "S"
						HoldEnds[2][i] = True
					elif line[i] == "Z":
						NoteLines[0][i] = "I"
					#Player 4
					elif line[i] == "2":
						NoteLines[1][i] = "S"
						HoldEnds[3][i] = True
					elif line[i] == "1":
						NoteLines[1][i] = "I"
					#Hold ends
					elif line[i] == "3":
						for j in range(len(HoldEnds)):
							if HoldEnds[j][i]:
								if j==0:
									NoteLines[0][i] = "3"
								elif j==1:
									NoteLines[1][i] = "3"
								elif j==2:
									NoteLines[0][i] = "E"
								else:
									NoteLines[1][i] = "E"
								HoldEnds[j][i] = False
					#Just shove it into the p1 track
					elif line[i] == "F":
						NoteLines[0][i] = "F"
					elif line[i] == "M":
						NoteLines[0][i] = "M"
					elif line[i] == "\n" or line[i] == "0":
						pass
					else:
						print("WARNING: Line contains unknown characters: "+line)

				for i in range(len(ChartTracks)):
					ChartTracks[i].append("".join(NoteLines[i]))
	finalLine = ""
	for i in range(len(ChartTracks)):
		for line in ChartTracks[i]:
			finalLine+=line+"\n"
		if i < len(ChartTracks)-1:
			finalLine+="&\n"
	#finalLine+=";\n"
	return finalLine

def doubleToHalfdouble(content:list[str]) -> str:
	insideNotesBlock = False
	currentMeasure = 0
	output = ""
	try:
		for line in content:
				
			if (line == "#NOTES:"): #Start of #NOTES block
				insideNotesBlock = True
				continue
				#print("#START")
			if (insideNotesBlock):
				if line[0] == ",":
					currentMeasure+=1
					output += line + "\n"
				elif (line[0] == ";"): #End of #NOTES block
					insideNotesBlock = False
					output += line + "\n"
					break
				elif represents_int(line[0]): #Is this even necessary?
					output += line[2:-2] + "\n"
				else:
					output += line + "\n"
	except IndexError:
		print("This chart seems to be malformed, there aren't enough notes in the track?")
		return ""
	return output

# with open('/media/arc/WayPastCool/PC GAMES/Rave It Out S2 (1.00 THE FINAL)/SongsPIU/55-PRIME/14C6 - [Full Song] Bad Apple!! (feat. Nomico)/14C6 - [Full Song] Bad Apple!! (feat. Nomico).ssc','r') as f:
# 	s = f.read()
# h,c,chartInfo=parseChart(s)
# #print(i)
# #n = 0
# for i in range(len(chartInfo)):
# 	if chartInfo[i]['is_sf2_co_op']:
# 		content = c[i]
# 		print(c[i])
# 		sys.exit(0)
# #print("Found "+str(n)+" co-op charts that need to be converted")


if __name__=='__main__':
	header = ""
	chartStrings=[]
	chartInfo=[]
	with open(sys.argv[1]) as file_read:
		print("Reading "+sys.argv[1])
		header,chartStrings,chartInfo=parse_ssc(file_read.read())

		n = 0
		for ci in chartInfo:
			if ci['is_sf2_co_op']:
				n+=1
		print("Found "+str(n)+" co-op charts that need to be converted")
		if n<1:
			sys.exit(0)
		for j in range(len(chartInfo)):
			if chartInfo[j]['is_sf2_co_op']:
				print("Converting chart "+chartInfo[j]["DESCRIPTION"])

				content = chartStrings[j].splitlines()
				
				if CONVERT_FOR_INFINITY:
					converted_line=convertForInfinity(content)
				else:
					converted_line = convertForRIO(content)

				#print(chartStrings[j])
				startNotes = chartStrings[j].index("#NOTES")
				endNotes = chartStrings[j][startNotes+10:].index(";") +startNotes+10
				print(startNotes)
				print(endNotes)
				#print(chartStrings[j][:startNotes])
				#print(finalLine[:1000]+"(...)")
				#print(chartStrings[j][endNotes:])

				chartStrings[j] = chartStrings[j][:startNotes].replace("pump-double",'pump-routine') + "#NOTES:\n"+converted_line+chartStrings[j][endNotes:]
				
	rename(sys.argv[1],sys.argv[1]+".old")
	dir = path.dirname(sys.argv[1])
	name,ext = path.splitext(sys.argv[1])
	fName = name+"_converted.ssc"
	with open(path.join(dir,fName),'w') as f:
		f.write(header)
		for s in chartStrings:
			f.write(s)
