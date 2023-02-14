#!/usr/bin/env python3

# this should later become a reader for a configuration file in /etc/

import socket
import subprocess
import os
from threading import Timer
import json

clusters = {
#  'production_c2f11': ["bu-c2f11-09-01.cms", "fu-c2f11-11-01.cms", "fu-c2f11-11-02.cms", "fu-c2f11-11-03.cms", "fu-c2f11-11-04.cms", ],        
#  'playback_c2f11': ["bu-c2f11-13-01.cms", "fu-c2f11-15-01.cms", "fu-c2f11-15-02.cms", "fu-c2f11-15-03.cms", "fu-c2f11-15-04.cms", ],          
#  'lookarea_c2f11': ["bu-c2f11-19-01.cms", ]                                                                                                   
  'production_c2a06': ["dqmrubu-c2a06-01-01.cms"],
  'playback_c2a06': ["dqmrubu-c2a06-03-01.cms", "dqmfu-c2b01-45-01.cms", "dqmfu-c2b02-45-01.cms"],
  'lookarea_c2a06': ["dqmrubu-c2a06-05-01.cms"]
}



def popen_timeout(cmd, seconds=10):
  kill = lambda process: process.kill()
  p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  timer = Timer(seconds, kill, [p])
  answer, stderr = " ... ",  " ... " 
  try:
    timer.start()
    answer, stderr = p.communicate()
  except Exception as error_log:
    answer = error_log
  if p.returncode : answer = stderr
  timer.cancel()
  return answer

def get_rpm_version(host, soft_path):
  if not host : return "host argument not defined"
  if not soft_path : return "soft_path argument not defined"
  return popen_timeout(["ssh " + host + " \"rpm -qf " + soft_path + "\""], 5)
  
def get_rpm_version_all(soft_path):
  answer = {}
  for key, lst in clusters.items():
    subanswer = {}
    for host in lst:
      version = get_rpm_version( host, soft_path )
      subanswer[ host ] = version
    answer[key] = subanswer

  return answer

def get_cmssw_info( cmssw_path ):
  if not cmssw_path : return "cmssw_path argument not defined"
  if cmssw_path[-1] != '/' : cmssw_path += '/'
  
  # 1. read CMSSW logs
  versions_raw = popen_timeout(["grep \"Selected release:\" --exclude-dir=\"*\" --include=*.log " + cmssw_path + "*"], 15)
  if not "Selected release:" in versions_raw : return versions_raw
  answer = versions_raw.split("Selected release: ")[-1]
  
  # 2. get PRs
  prs_raw = popen_timeout(["find " + cmssw_path + " -type f -name \"merge*log\""], 15)
  answer += "PRs :"

  # 3. get PRs merge status
  for fname in prs_raw.split("\n"):
    try:
      pr_id = os.path.basename( fname ).split(".")[1]
      status = popen_timeout(["grep \"Merge successful\" " + fname], 15)  
      answer += "\n " + pr_id;  
      answer += " ok" if status else " "
    except: continue

  # 4. get GTs
  gts_raw = popen_timeout(["grep -r \"GlobalTag.globaltag = \" " + cmssw_path + "src/DQM/Integration/python/config/*"], 15)
  if not "GlobalTag.globaltag" in gts_raw : return answer
  answer += "\nGTs:\n"
  for line in gts_raw.split("\n"):
    if "autoCond" in line : continue;
    answer += line + "\n"

  return answer

def get_dqm_clients( host, cmssw_path, clients_path ):
  if not host : return "host argument not defined"
  if not cmssw_path : return "cmssw_path argument not defined"
  available = popen_timeout(["ssh " + host + " \"find " + cmssw_path + " -type f -name *_cfg.py\""], 15)
  activated = popen_timeout(["ssh " + host + " \"find " + clients_path + " -type l\""], 15)

  available = [ os.path.basename( a ) for a in available.split("\n") if a ]
  activated = [ os.path.basename( a ) for a in activated.split("\n") if a ]
  answer = [ [a, a in activated] for a in available ]

  return answer

def change_dqm_client( host, cmssw_path, clients_path, client, state ):
  answer = None
  if state == "0":
    answer = popen_timeout(["ssh " + host + " \"sudo find " + clients_path + " -type l -name " + client + " -delete\""], 15)
  else :
    inp = os.path.join( cmssw_path, client )
    answer = popen_timeout(["ssh " + host + " \"cd " + clients_path + "/idle; sudo ln -s " + inp + "\""], 15)

  if not answer : return "Ok"
  return answer

def get_simulator_config(opts, this_host, simulator_host):
  if not simulator_host : return "host argument not defined"
  path = opts["simulator.conf"]
  cfg = None
  if this_host == simulator_host : cfg = popen_timeout(["cat " + path], 5)
  else                           : cfg = popen_timeout(["ssh " + simulator_host + " \"cat " + path + "\""], 5)
  return cfg

def update_config(cfg, key, value):
  if not key : return cfg
  if not key in cfg : return cfg
  if not value : return cfg
  cfg[ key ] = value
  return cfg

def write_config(opts, cfg): # only locally
  path = "/tmp/" + os.path.basename( opts["simulator.conf"] )
  f = open(path, "w")
  json.dump(cfg, f, sort_keys=True, indent=2)
  f.close()
  answer = popen_timeout([ "sudo cp " + path + " " + opts["simulator.conf"] ], 5)
  return answer

def get_simulator_runs(opts, this_host, simulator_host):
  if not simulator_host : return []
  cfg_json = get_simulator_config(opts, this_host, simulator_host)
  cfg = json.loads( cfg_json )
  path = os.path.dirname( cfg["source"] )
  runs_raw = None
  if this_host == simulator_host : runs_raw = popen_timeout(["ls -1d " + path + "/run*"], 5)
  else                           : runs_raw = popen_timeout(["ssh " + simulator_host + " \"ls -1d " + path + "/run*\""], 5)
  runs = []
  for run in runs_raw.split("\n"):
    runs += [ os.path.basename( run ) ]
  return runs

def restart_hltd(host):
  if not host : return "host argument not defined"
  answer = popen_timeout(["ssh " + host + " \"sudo -i /sbin/service hltd stop; sudo -i /sbin/service hltd start\""], 15)
  if not answer : return "Ok"
  return answer

def restart_fff(host):
  if not host : return "host argument not defined"
  answer = popen_timeout(["ssh " + host + " \"sudo systemctl restart fff_dqmtools.service\""], 15)
  if not answer : return "Ok"
  return answer

def get_txt_file(host, path, timeout=30):
  if not host : return "host argument not defined"
  if not path : return "path argument not defined"
  return popen_timeout(["ssh " + host + " \"cat " + path + "\""], timeout)

def get_host():
  host = socket.gethostname()
  host = host.lower()
  return host

def get_node():
  host = get_host()

  current = {
    "_all": clusters,
  }

  for key, lst in clusters.items():
    if host in lst:
      current["node"] = host
      current["nodes"] = lst
      current["label"] = key
      break

  return current

def host_wrapper(allow = []):
  """ This is decorator for function.
    Runs a function of the given hosts,
    just returns on others.
  """

  host = get_host()

  def run_wrapper(f):
    return f

  def noop_wrapper(f):
    def noop(*args, **kwargs):
      name = kwargs["name"]
      log = kwargs["logger"]

      log.info("The %s applet is not allowed to run on %s, disabling", name, host)
      return None

    return noop

  if host in allow:
    return run_wrapper
  else:
    return noop_wrapper

if __name__ == "__main__":
  print( get_node() )
