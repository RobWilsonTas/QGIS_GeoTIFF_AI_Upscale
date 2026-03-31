import numpy, subprocess, os, glob, time
from pathlib import Path
from qgis.core import QgsRasterLayer
from qgis.PyQt.QtWidgets import QMessageBox
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
startTime = time.time()

try:
    import ddddsr
except ImportError:
    print("ddddsr not found, installing...")
    qgisPython = str(Path(QgsApplication.prefixPath()).parent.parent) + r"\apps\Python312\python.exe"
    subprocess.run([qgisPython, "-m", "pip", "install", "ddddsr"], check=True)
    import ddddsr

"""
##########################################################
User options
"""

#Variable assignment
inImage                 = "C:/Temp/YourImage.tif"        #E.g 'C:/ImageEnhance/AerialImagery.tif'
approxPixelsPerTile     = 1500

#Options for compressing the images, ZSTD has the best speed but LZW is the most compatible
compressOptions         = 'COMPRESS=ZSTD|NUM_THREADS=ALL_CPUS|PREDICTOR=1|ZSTD_LEVEL=1|BIGTIFF=IF_SAFER|TILED=YES'

"""
##########################################################
Variable assignment for processing
"""

#Define the location of gdal and make sure its windows don't appear a hundred times
gdalwarpExe = str(Path(QgsApplication.prefixPath()).parent.parent / 'bin' / 'gdalwarp.exe')
gdalTranslateExe = str(Path(QgsApplication.prefixPath()).parent.parent / 'bin' / 'gdal_translate.exe')
startupinfo = subprocess.STARTUPINFO()
startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
startupinfo.wShowWindow = subprocess.SW_HIDE

#Get the location of the initial image for storage of processing files
rootProcessDirectory = str(Path(inImage).parent.absolute()).replace('\\','/') + '/'

#Set up the layer name for the raster calculations
inImageName = Path(inImage).stem[:8]
outImageName = inImageName

#Making a folder for processing each time, to avoid issues with locks
processDirectoryInstance = rootProcessDirectory + inImageName + 'Process' + '/'

#Creating all the subfolder variables
processDirectory                = processDirectoryInstance + '1Main/'
processBoundsDirectory          = processDirectoryInstance + '2TileBounds/'
processBoundsSmallerDirectory   = processDirectoryInstance + '3TileBoundsSmaller/'
processTileDirectory            = processDirectoryInstance + '4Tiles/'
aiOutputReffedDirectory         = processDirectoryInstance + '6AIOutputReffed/'
aiOutputDirectory               = processDirectoryInstance + '5AIOutput/'
aiOutputRefClipDirectory        = processDirectoryInstance + '7AIOutputRefClip/'
finalImageDir                   = processDirectoryInstance + '8Final/'

#Creating all the subfolders
if not os.path.exists(processDirectoryInstance):        os.mkdir(processDirectoryInstance)
if not os.path.exists(processDirectory):                os.mkdir(processDirectory)
if not os.path.exists(processBoundsDirectory):          os.mkdir(processBoundsDirectory)
if not os.path.exists(processBoundsSmallerDirectory):   os.mkdir(processBoundsSmallerDirectory)
if not os.path.exists(processTileDirectory):            os.mkdir(processTileDirectory)
if not os.path.exists(aiOutputReffedDirectory):         os.mkdir(aiOutputReffedDirectory)
if not os.path.exists(aiOutputDirectory):               os.mkdir(aiOutputDirectory)
if not os.path.exists(aiOutputRefClipDirectory):        os.mkdir(aiOutputRefClipDirectory)
if not os.path.exists(finalImageDir):                   os.mkdir(finalImageDir)

"""
####################################################################################
Final preps for processing
"""

#Get the pixel size and coordinate system of the raster
ras = QgsRasterLayer(inImage)
pixelSizeX = ras.rasterUnitsPerPixelX()
pixelSizeY = ras.rasterUnitsPerPixelY()
pixelSizeAve = (pixelSizeX + pixelSizeY) / 2
coordinateSystem = ras.crs().authid()

#Clear out the folders
for folder in [processDirectory, processBoundsDirectory, processBoundsSmallerDirectory, processTileDirectory]:
    for file in glob.glob(folder + '*'):
        try:
            os.remove(file)
        except BaseException as e:
            print(e)

"""
###############################################################################################
Get all of the tile extents
"""

#Get the extent of the image where there is alpha
processing.run("gdal:translate", {'INPUT':inImage,'TARGET_CRS':None,'NODATA':None,'COPY_SUBDATASETS':False,'OPTIONS':compressOptions,'EXTRA':'-b 4 -scale_1 128 255 -1000 1255','DATA_TYPE':0,'OUTPUT':processDirectory + inImageName + 'AlphaClean.tif'})
processing.run("gdal:polygonize", {'INPUT':processDirectory + inImageName + 'AlphaClean.tif','BAND':1,'FIELD':'DN','EIGHT_CONNECTEDNESS':False,'EXTRA':'','OUTPUT':processDirectory + inImageName + 'Extent.gpkg'})
processing.run("native:fixgeometries", {'INPUT':processDirectory + inImageName + 'Extent.gpkg','OUTPUT':processDirectory + inImageName + 'ExtentFix.gpkg'})
processing.run("native:extractbyexpression", {'INPUT':processDirectory + inImageName + 'ExtentFix.gpkg','EXPRESSION':' \"DN\" > 245','OUTPUT':processDirectory + inImageName + 'ExtentFilt.gpkg'})

#Determine the extent and coordinate system of the extent
fullExtentForCutline = processDirectory + inImageName + 'ExtentFilt.gpkg'
extentVector = QgsVectorLayer(processDirectory + inImageName + 'ExtentFilt.gpkg')
extentRectangle = extentVector.extent()
extentCrs = extentVector.sourceCrs()

#Then close the layer object so that QGIS doesn't unnecessarily hold on to it
QgsProject.instance().addMapLayer(extentVector, False)
QgsProject.instance().removeMapLayer(extentVector.id())

#Create a grid for dividing the image up into tiles
processing.run("native:creategrid", {'TYPE':2,'EXTENT':extentRectangle,'HSPACING':pixelSizeX * approxPixelsPerTile,
    'VSPACING':pixelSizeY * approxPixelsPerTile,'HOVERLAY':0,'VOVERLAY':0,'CRS':extentCrs,
    'OUTPUT':processDirectory + inImageName + 'ExtentFiltGrid.gpkg'})

#Buffer it out so that we have space for clipping 
processing.run("native:buffer", {'INPUT':processDirectory + inImageName + 'ExtentFiltGrid.gpkg','DISTANCE':pixelSizeAve * 100,
    'SEGMENTS':5,'END_CAP_STYLE':0,'JOIN_STYLE':1,'MITER_LIMIT':2,'DISSOLVE':False,
    'OUTPUT':processDirectory + inImageName + 'ExtentFiltGridBuffer.gpkg'})

#Only grab the part of the grid that will actually be relevant
processing.run("native:extractbylocation", {'INPUT':processDirectory + inImageName + 'ExtentFiltGridBuffer.gpkg','PREDICATE':[0,4,5],
    'INTERSECT':processDirectory + inImageName + 'ExtentFilt.gpkg', 'OUTPUT':processDirectory + inImageName + 'ExtentFiltGridBufferGrabbed.gpkg'})

#Clip this so we're not overrunning and getting AI to upscale an area of black
processing.run("native:clip", {'INPUT':processDirectory + inImageName + 'ExtentFiltGridBufferGrabbed.gpkg',
    'OVERLAY':processDirectory + inImageName + 'ExtentFilt.gpkg', 'OUTPUT':processDirectory + inImageName + 'ExtentFiltGridBufferGrabbedClip.gpkg'})

#Split it out so there is a different extent to work from for each instance of the raster clipping
processing.run("native:splitvectorlayer", {'INPUT':processDirectory + inImageName + 'ExtentFiltGridBufferGrabbedClip.gpkg',
    'FIELD':'id','FILE_TYPE':0,'OUTPUT':processBoundsDirectory})


"""
#########################################################################################
#Get the smaller versions of the tile extents
"""

#Buffer it out so that we have space for clipping 
processing.run("native:buffer", {'INPUT':processDirectory + inImageName + 'ExtentFiltGrid.gpkg','DISTANCE':pixelSizeAve * 75,
    'SEGMENTS':5,'END_CAP_STYLE':0,'JOIN_STYLE':1,'MITER_LIMIT':2,'DISSOLVE':False,'OUTPUT':processDirectory + inImageName + 'ExtentFiltGridBufferSmaller.gpkg'})

#Only grab the part of the grid that will actually be relevant
processing.run("native:extractbylocation", {'INPUT':processDirectory + inImageName + 'ExtentFiltGridBufferSmaller.gpkg',
    'PREDICATE':[0,4,5],'INTERSECT':processDirectory + inImageName + 'ExtentFilt.gpkg','OUTPUT':processDirectory + inImageName + 'ExtentFiltGridBufferSmallerGrabbed.gpkg'})

#Clip this so we're not overrunning and getting AI to upscale an area of black
processing.run("native:clip", {'INPUT':processDirectory + inImageName + 'ExtentFiltGridBufferSmallerGrabbed.gpkg',
    'OVERLAY':processDirectory + inImageName + 'ExtentFilt.gpkg','OUTPUT':processDirectory + inImageName + 'ExtentFiltGridBufferSmallerGrabbedClip.gpkg'})

#Split it out so there is a different extent to work from for each instance of the raster clipping
processing.run("native:splitvectorlayer", {'INPUT':processDirectory + inImageName + 'ExtentFiltGridBufferSmallerGrabbedClip.gpkg',
    'FIELD':'id','FILE_TYPE':0,'OUTPUT':processBoundsSmallerDirectory})


"""
#################################################################################################
Slice up the raster based on the larger tile extents
"""

#Take away the alpha band, this is not needed for the AI algorithm
layerWithoutAlphaBand = processing.run("gdal:translate", {'INPUT':inImage,'TARGET_CRS':None,'NODATA':None,'COPY_SUBDATASETS':False,'OPTIONS':compressOptions,'EXTRA':'-b 1 -b 2 -b 3','DATA_TYPE':0,'OUTPUT':QgsProcessing.TEMPORARY_OUTPUT})['OUTPUT']

boundsFiles = glob.glob(processBoundsDirectory + '/*.gpkg')

def clipTile(boundFile):
    try:
        boundName = Path(boundFile).stem
        outputFile = processTileDirectory + boundName + 'Tile.png'

        cmdLine = [gdalwarpExe, '-cutline', boundFile,
            '-crop_to_cutline', '-of', 'PNG',
            '-co', 'COMPRESS=LZW', layerWithoutAlphaBand, outputFile]
            
        subprocess.run(cmdLine, check=True, startupinfo=startupinfo)

    except BaseException as e:
        print(boundName + " error: " + str(e))

# run in parallel with threads so it stays in console
with ThreadPoolExecutor() as executor:
    list(executor.map(clipTile, boundsFiles))

print("All tiles clipped, ready for super resolution")

"""
#######################################################################
Apply the super resolution
"""

#Get all of the clipped png tiles
clippedTiles = glob.glob(processTileDirectory + '/*.png')

#Prep the super resolution thing
superResolutionProcessor = ddddsr.SR(model="waifu2x_photo", scale = 2, use_gpu=False)
def upscaleFile(inputFile):
    outputFile = aiOutputDirectory + Path(inputFile).stem + '.png'
    superResolutionProcessor(str(inputFile), str(outputFile))
    print("Upscaled saved to", outputFile)

#Run it in the background using cmd
with ThreadPoolExecutor() as executor: 
    list(executor.map(upscaleFile, clippedTiles))

"""
#######################################################################
Georef the results from the AI
"""

reffedFiles = glob.glob(aiOutputReffedDirectory + '*')
for f in reffedFiles:
    try:
        os.remove(f) 
    except BaseException as e:
        print(e)    


#This looks to see what .pngs are in the directory for the AI outputs to go, and runs through them
tileFiles = glob.glob(aiOutputDirectory + '/*.png')

def georefTile(tileFile):
    try:
        tileName = Path(tileFile).stem
        origRaster = QgsRasterLayer(processTileDirectory + tileName + '.png')
        bounds = origRaster.extent()
        outputRaster = aiOutputReffedDirectory + tileName + 'Reffed.tif'

        cmdLine = [gdalTranslateExe, '-a_ullr', str(bounds.xMinimum()), str(bounds.yMaximum()), str(bounds.xMaximum()), str(bounds.yMinimum()),
            '-a_srs', coordinateSystem, '-of', 'GTiff', '-co', 'COMPRESS=LZW', tileFile, outputRaster]
        subprocess.run(cmdLine, check=True, startupinfo=startupinfo)

    except BaseException as e:
        print(tileName + " error: " + str(e))

# run in parallel with threads so we stay in console
with ThreadPoolExecutor() as executor:
    list(executor.map(georefTile, tileFiles))

print("Ok the referencing section is done, now let's go for the clipping section")


"""
#######################################################################
Clipping the raster so that the overlap looks good
"""

reffedFiles = glob.glob(aiOutputReffedDirectory + '/*.tif')

def warpTile(inputRaster):
    try:
        tileName = Path(inputRaster).stem[:-10]
        outputRaster = aiOutputRefClipDirectory + tileName + 'RefClipTile.tif'
        maskFile = processBoundsSmallerDirectory + tileName + '.gpkg'

        cmdLine = [gdalwarpExe, '-cutline', maskFile, '-crop_to_cutline', '-of', 'GTiff',
            '-co', 'COMPRESS=LZW', inputRaster, outputRaster]
            
        subprocess.run(cmdLine, check=True, startupinfo=startupinfo)
        
    except BaseException as e:
        print(tileName + " error: " + str(e))

# Thread-based parallel execution
with ThreadPoolExecutor() as executor:
    list(executor.map(warpTile, reffedFiles))

"""
#######################################################################
Finally bring it all together into a final mosaic
"""

#Prepare to make a final mosaic where the alpha bands are respected
finalImageDir = finalImageDir.replace("/", "\\")
aiOutputRefClipDirectory = aiOutputRefClipDirectory.replace("/", "\\")

gdalOptionsFinal = '-co COMPRESS=LZW -co PREDICTOR=2 -co NUM_THREADS=ALL_CPUS -co BIGTIFF=IF_SAFER -co TILED=YES -multi --config GDAL_NUM_THREADS ALL_CPUS -wo NUM_THREADS=ALL_CPUS -overwrite'

#Final task
cmd = 'gdalwarp -of GTiff ' + gdalOptionsFinal + ' "' + aiOutputRefClipDirectory + '**.tif" "' + finalImageDir + outImageName + datetime.now().strftime("%Y%m%d%H%M") + '.tif" & timeout 5'
os.system(cmd)

print("Ok look under " + finalImageDir)
    

"""
#######################################################################
"""

#All done
endTime = time.time()
totalTime = endTime - startTime
print("Done, this took " + str(int(totalTime)) + " seconds")

