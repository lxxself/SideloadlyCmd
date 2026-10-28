"""Saved Xcode team metadata selection; no authentication or network calls."""
from pathlib import Path
import plistlib
import re
from .errors import CliError,ExitCode


def list_teams(account='xcode'):
    try:
        prefs=plistlib.loads((Path.home()/'Library/Preferences/com.apple.dt.Xcode.plist').read_bytes())
        owners={item['identifier'] for values in prefs.get('DVTDeveloperAccountManagerAppleIDLists',{}).values()
                for item in values if isinstance(item,dict) and isinstance(item.get('identifier'),str)}
        membership=prefs.get('IDEProvisioningTeamByIdentifier',{})
        if account!='xcode':
            selected=account.removeprefix('xcode:') if account.startswith('xcode:') else ''
            if selected not in owners: raise ValueError()
            visible={selected}
        else: visible=owners
        rows={}
        for owner in owners:
            for item in membership.get(owner,[]):
                team=item.get('teamID') if isinstance(item,dict) else None
                if not isinstance(team,str) or not re.fullmatch('[A-Z0-9]{10}',team): continue
                row=rows.setdefault(team,{'owners':set(),'visible':False,'kinds':set()})
                free=item.get('isFreeProvisioningTeam');kind=str(item.get('teamType','')).casefold()
                personal=free is True or kind=='personal team'
                conflict=(free is False and kind=='personal team') or (free is True and kind not in {'','personal team'})
                classification='unknown' if conflict else 'personal' if personal else 'other'
                row['owners'].add(owner);row['visible'] |= owner in visible;row['kinds'].add(classification)
        result=[]
        for team,row in sorted(rows.items()):
            if row['visible']:
                kind=next(iter(row['kinds'])) if len(row['kinds'])==1 else 'unknown'
                result.append({'team_id':team,'kind':kind,'is_personal':kind=='personal','owner_count':len(row['owners'])})
        return result
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        raise CliError('could not read saved Xcode teams for this account selector; check Xcode Accounts',ExitCode.NEEDS_USER,'xcode_teams_unavailable')


def select_team(team,account='xcode',offline=False):
    if team:
        if not re.fullmatch('[A-Z0-9]{10}',team):
            raise CliError('Team ID must contain 10 uppercase letters or digits',ExitCode.USAGE,'invalid_team')
        return team,'explicit'
    if offline:
        raise CliError('offline preparation requires an explicit --team and does not inspect account metadata',ExitCode.USAGE,'offline_team_required')
    rows=list_teams(account)
    personal=[row for row in rows if row['is_personal']]
    candidates=personal if personal else rows
    if len(candidates)!=1 or candidates[0]['owner_count']!=1:
        raise CliError('team selection is ambiguous or unavailable; run teams --json and specify --team',ExitCode.NEEDS_USER,'team_selection_required')
    return candidates[0]['team_id'],'unique_personal' if personal else 'unique_available'
