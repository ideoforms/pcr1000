import re

import glob
import time
import serial
import threading
from Queue import *

"""
pcr1000.py: A Python package to interface with the ICOM PCR-1000
serial-controlled radio receiver. 

TODO
Convert get_/set_ methods to @property
"""


class PCRCommand:
	pass

class PCRResponse:
	R_POWER = 0
	R_SQUELCH_STATUS = 1
	R_SIGNAL_STRENGTH = 2
	R_SIGNAL_CENTERING = 3
	R_DTMF_DETECTED = 4
	R_SIGNAL = 5
	R_COMMAND_OK = 6
	R_HAS_DSP = 7

	def __init__(self, code, args):
		self.code = code
		self.args = args

	def __str__(self):
		return "Response(%d, %s)" % (self.code, self.args)



class PCR1000:
	""" Represents a communications channel to a single ICOM PCR-1000 unit. """

	MODE_LSB	= 0
	MODE_USB	= 1
	MODE_AM	 = 2
	MODE_CW	 = 3
	MODE_UN	 = 4
	MODE_NFM	= 5
	MODE_WFM	= 6

	BAUD_300	= 0
	BAUD_1200   = 1
	BAUD_4800   = 2
	BAUD_9600   = 3
	BAUD_19200  = 4
	BAUD_38400  = 5

	FLT_3K	  = 0
	FLT_6K	  = 1
	FLT_15K	 = 2
	FLT_50K	 = 3
	FLT_230K	= 4


	def __init__(self, debug = False):
		self.port = None
		self.frequency = None
		self.mode = PCR1000.MODE_WFM
		self.filter = PCR1000.FLT_230K
		self.read_thread = None
		self.write_queue = None
		self.write_thread = None
		self.handlers = { }
		self.debug = debug
		self.parser = BufferedParser(debug = debug)
		self.sleep = 0.05
		self.synchronous = False
		self.started = False

		# see if we can find a default FTDI-style interface ID
		self.port_name = None
		interfaces = glob.glob("/dev/cu.usbserial-*")
		interfaces = sorted(interfaces)
		if interfaces:
			self.port_name = interfaces[0]

	def trace(self, text):
		if self.debug:
			print text

	def is_open(self):
		return True if self.port else False

	def open(self, port_name = None):
		""" Open a connection to the named serial device (eg /dev/tty.*) """
		try:
			if port_name is not None:
				self.port_name = port_name
			self.trace("open: %s" % self.port_name)
			if self.port is not None:
				self.trace("port already open, bailing")
				return 
			self.port = serial.Serial(baudrate = 9600, timeout = 0.1)
			self.port.setPort(self.port_name)
			self.port.open()
			self.background_poll()
			return self.port.getCTS()
		except Exception, e:
			print "couldn't open serial port: %s" % e
			self.port = None
			return False

	def close(self):
		if self.port:
			self.port.close()
		self.port = None
		if self.read_thread:
			self.read_thread.join()
		if self.write_thread:
			self.write_thread.join()

	def start(self, baud = BAUD_9600, volume = 0.5, squelch = 0.0, tsquelch = 0.0):
		""" Start up the PCR-1000 and set initial settings. """
		if self.started:
			print "*** already started, bailing"
			return 
		if not self.port:
			self.open()

		sleep = self.sleep
		self.set_power(True)
		time.sleep(sleep)
		self.get_alive()
		time.sleep(sleep)
		self.set_auto_update(True)
		time.sleep(sleep)
		self.set_baud(baud)
		time.sleep(sleep)
		self.set_squelch(squelch)
		time.sleep(sleep)
		self.set_tsquelch(tsquelch)
		time.sleep(sleep)
		self.set_volume(volume)
		time.sleep(sleep)

	def stop(self):
		""" Turn off the device. """
		self.started = False
		self.set_power(False)

	def background_poll(self):
		# set daemon mode to True so this thread dies when the main thread
		# is killed
		if self.read_thread:
			print "*** already running background poll, refusing to start another thread"
			return
		else:
			self.read_thread = threading.Thread(target = self.read_serial)
			self.read_thread.daemon = True
			self.read_thread.start()

			self.write_thread = threading.Thread(target = self.write_serial)
			self.write_thread.daemon = True
			self.write_thread.start()
			print "started read and write threads"

	def write_serial(self):
		self.write_queue = Queue()
		while True:
			try:
				command = self.write_queue.get()
				print "command: %s (port %s)" % (command, self.port)
				self.port.write("%s\r\n" % command)
				self.trace("[WRITE] write: %s" % command)
				self.write_queue.task_done()
			except Exception, e:
				print "Failed to operate on job: %s (type %s)" % (e, type(e))

	def read_serial(self):
		if self.port is None:
			return

		while self.port and self.port.isOpen():
			n = self.port.inWaiting()
			while n:
				try:
					text = self.port.read(size = n)
					text = text.replace("\r", "")
					text = text.replace("\n", "")
					text = text.strip()
					if len(text) == 0: continue
					self.trace("[POLL] read: %s" % text)
					responses = self.parser.parse(text)
					for response in responses:
						self.handle(response)
				except serial.SerialException, e:
					print "*** error reading serial: %s" % e
					# self.port.close()
					break
				n = self.port.inWaiting()
			time.sleep(0.02)

	def handle(self, response):
		if response.code == PCRResponse.R_POWER:
			self.trace("[RSP] power: %d" % response.args[0])
		if response.code == PCRResponse.R_SQUELCH_STATUS:
			self.trace("[RSP] squelch status: %d" % response.args[0])
		if response.code == PCRResponse.R_SIGNAL_STRENGTH:
			self.trace("[RSP] signal strength: %.3f" % response.args[0])
		if response.code == PCRResponse.R_COMMAND_OK:
			self.trace("[RSP] command ok: %d" % response.args[0])

		self.query_rv = response

		rv = True
		if response.code in self.handlers and self.handlers[response.code]:
			rv = self.handlers[response.code](response, self)

		return rv

	def add_handler(self, response_code, handler):
		self.handlers[response_code] = handler

	def on_power(self, handler):
		self.add_handler(PCRResponse.R_POWER, handler)
	def on_squelch_status(self, handler):
		self.add_handler(PCRResponse.R_SQUELCH_STATUS, handler)
	def on_signal_strength(self, handler):
		self.add_handler(PCRResponse.R_SIGNAL_STRENGTH, handler)
	def on_command_ok(self, handler):
		self.add_handler(PCRResponse.R_COMMAND_OK, handler)

	def write(self, command, sync = None):
		if not self.port:
			return
		if sync is None:
			sync = self.synchronous

		command = command.upper()
		print "[WRITE] write: %s" % command
		self.write_queue.put(command)

		if sync:
			return self.await_response()

	def tune(self, frequency, mode = None, filter = None, sync = False):
		self.set_frequency(frequency, False)
		if mode:
			self.set_mode(mode, False)
		if filter:
			self.set_filter(filter, False)

		return self.retune(sync = sync)

	def await_response(self):
		self.query_rv = None
		counter = 0
		counter_max = 30
		while self.query_rv is None and counter < counter_max:
			# print "...sleep... (%d)" % counter
			time.sleep(0.01)
			counter += 1
		if counter >= counter_max:
			self.trace("timed out waiting for serial response!")
		return self.query_rv

	def set_port_name(self, port_name):
		self.port_name = port_name
		if self.is_open():
			self.close()
			self.open()

	def set_frequency(self, frequency, retune = True, sync = False):
		self.frequency = int(frequency)
		if retune:
			self.retune()

	def set_filter(self, filter, retune = True):
		self.filter = int(filter)
		if retune:
			self.retune()

	def set_mode(self, mode, retune = True):
		self.mode = mode
		if retune:
			self.retune()



	#------------------------------------------------------------------------------
	# PCR1000 commands
	#------------------------------------------------------------------------------

	def set_power(self, on):
		self.write("H10%d" % int(on))

	def set_auto_update(self, update):
		self.write("G30%d" % int(update))

	def set_baud(self, baud):
		self.write("G10%d" % baud)

	def retune(self, sync = None):
		# K0GMMMKKKHHHmmff00
		if self.frequency is not None:
			command = "K0%010d%02d%02d00" % (self.frequency, self.mode, self.filter)
			return self.write(command, sync)

	def set_volume(self, volume, sync = None):
		self.write("J40%02x" % int(volume * 255), sync)

	def set_squelch(self, squelch, sync = None):
		command = "J41%02x" % int(squelch * 255)
		self.write(command, sync)

	def set_ifshift(self, value):
		self.write("J43%02d" % value)

	def set_agc(self, value):
		self.write("J45%02d" % value)

	def set_nb(self, value):
		self.write("J46%02d" % value)

	def set_att(self, value):
		self.write("J47%02d" % value)

	def set_afc(self, value):
		self.write("J50%02d" % value)

	def set_tsquelch(self, value):
		self.write("J51%02d" % value)

	def get_alive(self):
		self.write("H1?")

	def get_squelch_status(self):
		self.write("I0?")

	def get_signal_strength(self, sync = None):
		return self.write("I1?", sync)

	def get_signal_centering(self, sync = None):
		return self.write("I2?", sync)

	def is_dsp_present(self, sync = None):
		return self.write("GD?", sync)

	def set_dsp_status(self, on = True, anr = 1.0, notch = True):
		command = "J8001J81%02xJ82%02xJ83%02x" % (int(on), int(anr * 15), int(notch))
		self.write(command)


class BufferedParser:
	def __init__(self, debug = False):
		self.buffer = ""
		self.debug = debug

	def trace(self, text):
		if self.debug:
			print text

	def parse(self, string):
		self.buffer += string
		responses = []
		self.trace("[BUF] buffer %s" % self.buffer)

		while len(self.buffer) >= 4:
			# if self.buffer[0] == "I":
			if re.match("I[0-3][0-9A-F]{2}", self.buffer):
				# I0?? squelch status   - returns 04 = Closed, 07 = Open
				# I1?? signal strength  - returns 00 to FF
				# I2?? signal centering - returns 00 = Low, 80 = Centered, FF = High
				# I3?? DTMF Tone
				code = self.buffer[1]
				response = None
				if code == "0":
					args = [ 1 if self.buffer[2] == "7" else 0 ]
					response = PCRResponse(PCRResponse.R_SQUELCH_STATUS, args)
				elif code == "1":
					# parse hex param (00..FF) as a float (0..1)
					# never seen a signal strength value about 12...
					hvalue = self.buffer[2:4]
					value = int("0x%s" % hvalue, 0) / 256.0
					response = PCRResponse(PCRResponse.R_SIGNAL_STRENGTH, [ value ])
				elif code == "2":
					hvalue = self.buffer[2:4]
					value = int("0x%s" % hvalue, 0) / 255.0
					response = PCRResponse(PCRResponse.R_SIGNAL_CENTERING, [ value ])
				elif code == "3":
					hvalue = self.buffer[2:4]
					value = int("0x%s" % hvalue, 0) / 255.0
					response = PCRResponse(PCRResponse.R_DTMF_DETECTED, [ value ])

				if response:
					responses.append(response)
				self.buffer = self.buffer[4:]
					
			# elif self.buffer[0] == "H":
			elif re.match("H10[01?]", self.buffer):
				# H101 = on
				# H100 = off
				try:
					value = int(self.buffer[3])
					response = PCRResponse(PCRResponse.R_POWER, [ value ])
					responses.append(response)
				except:
					# sometimes we receive an initial stream containing
					# "H10H100H100" -- ignore these.
					pass
				self.buffer = self.buffer[4:]

			elif re.match("G[0-9][0-9].", self.buffer):
				# G???: signal update
				code = self.buffer[1]
				value = self.buffer[3]
				if code == "0":
					response = PCRResponse(PCRResponse.R_COMMAND_OK, [ value == "0" ])
					responses.append(response)
				elif code == "3":
					pass
				self.buffer = self.buffer[4:]

			# DSP check
			elif re.match("D0[01]", self.buffer):
				# D00 = no DSP
				# D01 = has DSP
				try:
					value = int(self.buffer[2])
					response = PCRResponse(PCRResponse.HAS_DSP , [ value ])
					responses.append(response)
				except:
					pass
				self.buffer = self.buffer[3:]

			# elif self.buffer[0] == "N":
				# N???: ???
			#	self.buffer = self.buffer[4:]

			else:
				if re.match("[GHIN]", self.buffer):
					print "*** known command but unrecognized args: %s" % self.buffer
					self.buffer = self.buffer[1:]
				while len(self.buffer) > 0 and not re.match("[GHIN]", self.buffer):
					self.trace("[BUF] *** skipping char: %s" % self.buffer[0])
					self.buffer = self.buffer[1:]

		return responses

if __name__ == "__main__":
	main()

