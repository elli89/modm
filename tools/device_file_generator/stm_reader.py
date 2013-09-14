# -*- coding: utf-8 -*-
# Copyright (c) 2013, Roboterclub Aachen e.V.
# All rights reserved.
# 
# The file is part of the xpcc library and is released under the 3-clause BSD
# license. See the file `LICENSE` for the full license governing this code.
# -----------------------------------------------------------------------------

from reader import XMLDeviceReader
from peripheral import Peripheral
from register import Register
from lxml import etree

import os, sys, math, glob, re
# add python module logger to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'logger'))
from logger import Logger
# add python module device files to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'device_files'))
from device_identifier import DeviceIdentifier

class STMDeviceReader(XMLDeviceReader):
	""" STMDeviceReader
	This STM specific part description file reader knows the structure and
	translates the data into a platform independent format.
	"""

	def __init__(self, file, logger=None):
		# we need to combile several xml files
		
		# The memory files are actually the best starting point, since they
		# are named most specifically after the device
		memoryFile = XMLDeviceReader(file, logger)
		memory = memoryFile.query("//Root")[0]
		# this name string contains all relevant information
		name = memory.get('name')
		# the device identifier parses it
		dev = DeviceIdentifier(name.lower())
		
		# now we have to search the correct peripheral configuration files
		# they use the same file for several size_ids, therefore we have to
		# filter them manually
		device = ''
		files = glob.glob(os.path.join(os.path.dirname(file), '..', name[:-1] + '*'))
		for deviceFile in files:
			fileName = os.path.basename(deviceFile).replace(name[:-1], '').replace('.xml', '')
			# Tx -> LQFP, Ux -> VFQFPN, Hx -> LFBGA, Yx -> WLCSP
			# we only take the QFP as reference
			if fileName[-2:] in ['Tx', 'Ux']:
				if fileName[0] != '(':
					if fileName[0] in [name[-1:], 'x']:
						device = deviceFile
						break
				else:
					# (S-I-Z-E)Tx
					sizes = fileName[1:-3].split('-')
					if name[-1:] in sizes:
						device = deviceFile
						break
		
		if device == '' and logger:
			logger.error("STMDeviceReader: Could not find device file for device '%s'" % dev.string)
			logger.error("STMDeviceReader: Possible files are: " + str([os.path.basename(f) for f in files]))
			return
		
		# this peripheral file is the actual, important file to work with
		XMLDeviceReader.__init__(self, device, logger)
		self.name = name
	
		# Some information about core and architecture can be found in the
		# propertyGroup.xml file
		propertyGroup = XMLDeviceReader(os.path.join(os.path.dirname(file), 'propertyGroups.xml'), self.log)
		architecture = propertyGroup.query("//groupEntry[@name='%s']/property[@name='arm_architecture']" % name)[0].get('value').lower()
		core = propertyGroup.query("//groupEntry[@name='%s']/property[@name='arm_core_type']" % name)[0].get('value').lower()
		defines = propertyGroup.query("//groupEntry[@name='%s']/cdefine" % name)
		for define in defines:
			cdef = define.get('name')
			if cdef.startswith('STM32'):
				self.properties['define'] = cdef
		
		self.properties['id'] = dev
		self.properties['core'] = core
		self.properties['architecture'] = architecture

		flash = memoryFile.query("//MemorySegment[@name='FLASH']")[0].get('size')
		ram = memoryFile.query("//MemorySegment[@name='RAM']")[0].get('size')
		# only the STM32F4 has Core Coupled Memory
		if dev.family == 'f4':
			data_sram = memoryFile.query("//MemorySegment[@name='DATA_SRAM']")[0].get('size')
		else:
			data_sram = '0'
		self.properties['flash'] = int(flash, 16)
		self.properties['ram'] = int(ram, 16) + int(data_sram, 16)

		self.properties['gpios'] = gpios = []
		self.properties['peripherals'] = peripherals = []
		self.properties['modules'] = modules = []

		self.modules = self.query("//IP/@InstanceName")
		self.log.debug("Available Modules are:\n" + self._modulesToString())
		package = self.query("/Mcu/@Package")[0]
		self.properties['pin-count'] = re.findall('[0-9]+', package)[0]
		self.properties['package']   = re.findall('[A-Z]+', package)[0]
		
		for m in self.modules:
			if any(m.startswith(per) for per in ['TIM', 'UART', 'USART', 'ADC', 'DAC', 'CAN', 'SPI', 'I2C']):
				modules.append(m)
		
		invertMode = {'out': 'in', 'in': 'out', 'io': 'io'}
		nameToMode = {'rx': 'in', 'tx': 'out', 'cts': 'in', 'rts': 'out', 'ck': 'out',	# Uart
					 'miso': 'in', 'mosi': 'out', 'nss': 'io', 'sck': 'out',	# Spi
					 'scl': 'out', 'sda': 'io'}	# I2c
		nameToAf = {'SYS': '0',
					'TIM1': '1', 'TIM2': '1',
					'TIM3': '2', 'TIM4': '2', 'TIM5': '2',
					'TIM8': '3', 'TIM9': '3', 'TIM10': '3', 'TIM11': '3',
					'I2C1': '4', 'I2C2': '4', 'I2C3': '4',
					'SPI1': '5', 'SPI2': '5',
					'SPI3': '6',
					'USART1': '7', 'USART2': '7', 'USART3': '7',
					'UART4': '8', 'UART5': '8', 'USART6': '8',
					'CAN1': '9', 'CAN2': '9', 'TIM12': '9', 'TIM13': '9', 'TIM14': '9',
					'OTG_FS': '10',
					'ETH_MII': '11', 'ETH_RMII': '11',
					'FSMC_NAND16': '12', 'FSMC_NOR_MUX': '12', 'SDIO': '12', 'OTG_HS_FS': '12',
					'DCMI': '13'}

		for pin in self.query("//Pin[@Type='I/O']"):
			name = pin.get('Name')
			if '-' in name:
				name = name.split('-')[0]
			elif '/' in name:
				name = name.split('/')[0]
			
			gpio = {'port': name[1:2], 'id': name[2:], 'af': []}
			
			for signal in [s.get('Name') for s in pin if s.get('Name') != 'GPIO']:
				raw_names = signal.split('_')
				instance = raw_names[0][-1]
				name = raw_names[1].lower()
				
				if signal.startswith('USART') or signal.startswith('UART'):
					af = {'peripheral' : 'Uart' + instance,
						  'name': name.capitalize(),
						  'type': nameToMode[name],
						  'id': nameToAf[raw_names[0]]}
					gpio['af'].append(af)
				
				elif signal.startswith('SPI'):
					af = {'peripheral' : 'SpiMaster' + instance,
						  'name': name.capitalize(),
						  'type': nameToMode[name],
						  'id': nameToAf[raw_names[0]]}
					gpio['af'].append(af)
					invertName = {'miso': 'somi', 'mosi': 'simo', 'nss': 'nss', 'sck': 'sck'}
					af2 = {'peripheral' : 'SpiSlave' + instance,
						  'name': invertName[name].capitalize(),
						  'type': invertMode[nameToMode[name]],
						  'id': nameToAf[raw_names[0]]}
					gpio['af'].append(af2)
				
				if signal.startswith('CAN'):
					af = {'peripheral' : 'Can' + instance,
						  'name': name.capitalize(),
						  'type': nameToMode[name],
						  'id': nameToAf[raw_names[0]]}
					gpio['af'].append(af)
				
				if signal.startswith('I2C'):
					if name in ['scl', 'sda']:
						af = {'peripheral' : 'I2cMaster' + instance,
							  'name': name.capitalize(),
							  'type': nameToMode[name],
							  'id': nameToAf[raw_names[0]]}
						gpio['af'].append(af)
				
				if signal.startswith('TIM'):
					for tname in raw_names[1:]:
						nice_name = 'ExternalTrigger'
						output_type = 'in'
						if 'CH' in tname:
							nice_name = tname.replace('CH', 'Channel')
							output_type = 'io'
						elif 'BKIN' in tname:
							nice_name = 'BreakIn'
						af = {'peripheral' : 'Timer' + instance,
							  'name': nice_name,
							  'type': output_type,
							  'id': nameToAf[raw_names[0]]}
						gpio['af'].append(af)
				
				if signal.startswith('ADC'):
					if 'exti' not in name:
						af = {'peripheral' : 'Adc' + instance,
							  'name': name.replace('in', 'Channel').capitalize(),
							  'type': 'in'}
						gpio['af'].append(af)
			
			gpios.append(gpio)

	def _modulesToString(self):
		string = ""
		char = self.modules[0][0:1]
		for module in self.modules:
			if not module.startswith(char):
				string += "\n"
			string += module + " \t"
			char = module[0][0:1]
		return string

	def __repr__(self):
		return self.__str__()

	def __str__(self):
		return "STMDeviceReader(" + self.name + ")"
