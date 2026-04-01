
from queue import Queue,Empty,Full
import subprocess as sp
import json
import threading
from .encodingUtils import cleanFilenameForFfmpeg

try:
  from .platformUtils import resource_path, tool_command
except Exception:
  from platformUtils import resource_path, tool_command

class FaceDetectionService():


  def __init__(self,globalStatusCallback=print,globalOptions={}):
    self.globalStatusCallback = globalStatusCallback
    self.faceDetectRequestQueue = Queue()
    self.globalOptions=globalOptions
    self.pigoPresent = False
    try:
      sp.run('pigo',stderr=sp.DEVNULL,stdout=sp.DEVNULL)
      self.pigoPresent=True
    except:
      self.pigoPresent = False
    self.cache = {}

    def processRectRequests():
      if self.pigoPresent:
        while 1:
          sourceFile,filterStack,timestamp,callback = self.faceDetectRequestQueue.get()

          try:
            
            framePng = sp.run([tool_command('ffmpeg'), '-ss', str(timestamp), '-i', cleanFilenameForFfmpeg(sourceFile),  '-vframes', '1', '-c:v', 'png', '-f', 'image2pipe', '-'],stdout=sp.PIPE,stderr=sp.PIPE)
            rects    = sp.run(['pigo', '-in', '-', '-cf', resource_path('resources', 'cascade', 'facefinder'), '-plc', resource_path('resources', 'cascade', 'puploc'), '-json', '-', '-out', 'empty'],input=framePng.stdout,stderr=sp.PIPE,stdout=sp.PIPE)
            print(rects)
            rects    = json.loads(rects.stdout)

            for line in framePng.stderr.split(b'\n'):
              if b'Stream ' in line and b'Video:' in line:
                print(line)

            callback(sourceFile,timestamp,rects)
          except Exception as e:
            print(e)
            callback(sourceFile,timestamp,[])

          self.faceDetectRequestQueue.task_done()

    self.faceWorkerThread = threading.Thread(target=processRectRequests,daemon=True)
    self.faceWorkerThread.start()

  def clearCache(self):
    self.cache = {}

  def faceDetectEnabled(self):
    return self.pigoPresent

  def getFaceBoundingRect(self,sourceFile,filterStack,timestamp,callback):
    if self.pigoPresent:
      self.faceDetectRequestQueue.put( (sourceFile,filterStack,timestamp,callback) )
    else:
      callback([])

if __name__ == '__main__':
  print("FaceDetectionService is intended to be used from the application runtime.")
