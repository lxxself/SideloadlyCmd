property maxDepth : 7
property maxNodes : 320
property visitedNodes : 0

using terms from application "System Events"

on run argv
	if (count of argv) < 1 then return "ERROR¦missing action"
	set actionName to item 1 of argv
	if actionName is "snapshot" then return my snapshotUI()
	if actionName is "refresh-list" then return my snapshotRefreshMenu()
	if actionName is "activate" then return my activateApp()
	if actionName is "click" then
		if (count of argv) is not 3 then return "ERROR¦click expects role and name"
		return my clickUnique(item 2 of argv, item 3 of argv)
	end if
	if actionName is "click-value" then
		if (count of argv) is not 3 then return "ERROR¦click-value expects role and value"
		return my clickUniqueValue(item 2 of argv, item 3 of argv)
	end if
	if actionName is "set" then
		if (count of argv) is not 4 then return "ERROR¦set expects role, name, and value"
		return my setUnique(item 2 of argv, item 3 of argv, item 4 of argv)
	end if
	return "ERROR¦unknown action"
end run

on snapshotUI()
	set outputRows to {"APP¦RUNNING"}
	set windowNumber to 0
	set visitedNodes to 0
	tell application "System Events"
		if not (exists application process "Sideloadly") then return "APP¦NOT_RUNNING"
		tell application process "Sideloadly"
			repeat with theWindow in windows
				set windowNumber to windowNumber + 1
				set windowTitle to ""
				try
					set windowTitle to my cleanField(name of theWindow as text)
				end try
				set end of outputRows to "WINDOW¦" & (windowNumber as text) & "¦" & windowTitle
				set outputRows to outputRows & my describeNode(theWindow, 0)
				if visitedNodes >= maxNodes then exit repeat
			end repeat
		end tell
	end tell
	set AppleScript's text item delimiters to linefeed
	set outputText to outputRows as text
	set AppleScript's text item delimiters to ""
	return outputText
end snapshotUI

on snapshotRefreshMenu()
    set outputRows to {"REFRESH¦OK"}
    tell application "System Events"
        if not (exists application process "sideloadly-daemon") then return "REFRESH¦UNAVAILABLE"
        tell application process "sideloadly-daemon"
            if (count of menu bars) < 2 then return "REFRESH¦UNAVAILABLE"
            set statusItems to every menu bar item of menu bar 2 whose description is "status menu"
            if (count of statusItems) is not 1 then return "REFRESH¦UNAVAILABLE"
            set statusItem to item 1 of statusItems
            try
                if not (exists menu 1 of statusItem) then click statusItem
                set installMenu to menu 1 of menu item "Current Installs" of menu 1 of statusItem
                repeat with appItem in menu items of installMenu
                    set end of outputRows to "ITEM¦" & my cleanField(name of appItem as text)
                end repeat
            on error
                return "REFRESH¦UNAVAILABLE"
            end try
        end tell
        key code 53
    end tell
    set AppleScript's text item delimiters to linefeed
    set outputText to outputRows as text
    set AppleScript's text item delimiters to ""
    return outputText
end snapshotRefreshMenu

on activateApp()
	tell application "System Events"
		if not (exists application process "Sideloadly") then return "APP¦NOT_RUNNING"
		tell application process "Sideloadly" to set frontmost to true
	end tell
	return "ACTION¦OK"
end activateApp

on describeNode(theNode, depthValue)
	set visitedNodes to visitedNodes + 1
	if visitedNodes > maxNodes then return {}
	set roleName to ""
	set subroleName to ""
	set elementName to ""
	set elementValue to ""
	set descriptionText to ""
	try
		set descriptionText to description of theNode as text
	end try
	try
		set roleName to role of theNode as text
	end try
	try
		set subroleName to subrole of theNode as text
	end try
	try
		set elementName to name of theNode as text
	end try
	if roleName is not "AXSecureTextField" and roleName is not "AXPasswordField" then
		try
			set elementValue to value of theNode as text
		end try
	end if
	set rowText to "NODE¦" & (depthValue as text) & "¦" & my cleanField(roleName) & "¦" & my cleanField(subroleName) & "¦" & my cleanField(elementName) & "¦" & my cleanField(elementValue) & "¦" & my cleanField(descriptionText)
	set rowList to {rowText}
	if depthValue < maxDepth and visitedNodes < maxNodes then
		try
			set childElements to every UI element of theNode
			repeat with childElement in childElements
				set rowList to rowList & my describeNode(childElement, depthValue + 1)
				if visitedNodes >= maxNodes then exit repeat
			end repeat
		end try
	end if
	return rowList
end describeNode

on clickUnique(roleWanted, nameWanted)
	set visitedNodes to 0
	set matchingElements to {}
	tell application "System Events"
		if not (exists application process "Sideloadly") then return "APP¦NOT_RUNNING"
		tell application process "Sideloadly"
			repeat with theWindow in windows
				set matchingElements to matchingElements & my findElements(theWindow, roleWanted, nameWanted, 0)
				if visitedNodes >= maxNodes then exit repeat
			end repeat
		end tell
		if (count of matchingElements) is 0 then return "ACTION¦NOT_FOUND"
		if (count of matchingElements) is not 1 then return "ACTION¦AMBIGUOUS"
		try
			my pressElement(item 1 of matchingElements)
			return "ACTION¦OK"
		on error
			return "ACTION¦FAILED"
		end try
	end tell
end clickUnique

on clickUniqueValue(roleWanted, valueWanted)
	set visitedNodes to 0
	set matchingElements to {}
	tell application "System Events"
		if not (exists application process "Sideloadly") then return "APP¦NOT_RUNNING"
		tell application process "Sideloadly"
			repeat with theWindow in windows
				set matchingElements to matchingElements & my findValueElements(theWindow, roleWanted, valueWanted, 0)
				if visitedNodes >= maxNodes then exit repeat
			end repeat
		end tell
		if (count of matchingElements) is 0 then return "ACTION¦NOT_FOUND"
		if (count of matchingElements) is not 1 then return "ACTION¦AMBIGUOUS"
		try
			my pressElement(item 1 of matchingElements)
			return "ACTION¦OK"
		on error
			return "ACTION¦FAILED"
		end try
	end tell
end clickUniqueValue

on setUnique(roleWanted, nameWanted, newValue)
	if roleWanted is "AXSecureTextField" or roleWanted is "AXPasswordField" then return "ACTION¦SECURE_FIELD_BLOCKED"
	set visitedNodes to 0
	set matchingElements to {}
	tell application "System Events"
		if not (exists application process "Sideloadly") then return "APP¦NOT_RUNNING"
		tell application process "Sideloadly"
			repeat with theWindow in windows
				set matchingElements to matchingElements & my findElements(theWindow, roleWanted, nameWanted, 0)
				if visitedNodes >= maxNodes then exit repeat
			end repeat
		end tell
		if (count of matchingElements) is 0 then return "ACTION¦NOT_FOUND"
		if (count of matchingElements) is not 1 then return "ACTION¦AMBIGUOUS"
		try
			set value of item 1 of matchingElements to newValue
			return "ACTION¦OK"
		on error
			return "ACTION¦FAILED"
		end try
	end tell
end setUnique

on findElements(theNode, roleWanted, nameWanted, depthValue)
	set visitedNodes to visitedNodes + 1
	if visitedNodes > maxNodes then return {}
	set foundElements to {}
	set roleName to ""
	set elementName to ""
	set descriptionText to ""
	try
		set descriptionText to description of theNode as text
	end try
	try
		set roleName to role of theNode as text
	end try
	try
		set elementName to name of theNode as text
	end try
	if roleName is roleWanted and (elementName is nameWanted or descriptionText is nameWanted) then set end of foundElements to theNode
	if depthValue < maxDepth and visitedNodes < maxNodes then
		try
			set childElements to every UI element of theNode
			repeat with childElement in childElements
				set foundElements to foundElements & my findElements(childElement, roleWanted, nameWanted, depthValue + 1)
				if visitedNodes >= maxNodes then exit repeat
			end repeat
		end try
	end if
	return foundElements
end findElements

on findValueElements(theNode, roleWanted, valueWanted, depthValue)
	set visitedNodes to visitedNodes + 1
	if visitedNodes > maxNodes then return {}
	set foundElements to {}
	set roleName to ""
	set elementValue to ""
	try
		set roleName to role of theNode as text
	end try
	if roleName is not "AXSecureTextField" and roleName is not "AXPasswordField" then
		try
			set elementValue to value of theNode as text
		end try
	end if
	if roleName is roleWanted and elementValue is valueWanted then set end of foundElements to theNode
	if depthValue < maxDepth and visitedNodes < maxNodes then
		try
			set childElements to every UI element of theNode
			repeat with childElement in childElements
				set foundElements to foundElements & my findValueElements(childElement, roleWanted, valueWanted, depthValue + 1)
				if visitedNodes >= maxNodes then exit repeat
			end repeat
		end try
	end if
	return foundElements
end findValueElements

on pressElement(theElement)
	tell application "System Events"
		set roleName to role of theElement as text
		if roleName is "AXButton" or roleName is "AXCheckBox" then
			tell application process "Sideloadly" to set frontmost to true
			set value of attribute "AXFocused" of theElement to true
			if (value of attribute "AXFocused" of theElement) is not true then error "Control focus could not be confirmed"
			key code 49
		else
			click theElement
		end if
	end tell
end pressElement

on cleanField(inputText)
	set AppleScript's text item delimiters to {"¦", tab, linefeed, return}
	set pieces to text items of inputText
	set AppleScript's text item delimiters to " "
	set cleanedText to pieces as text
	set AppleScript's text item delimiters to ""
	return cleanedText
end cleanField

end using terms from
