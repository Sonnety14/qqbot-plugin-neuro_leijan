"""Reviewed, exact-match patches for the original 3.10.3 Vue components."""
def patch_ui(text):
    patches = [
        ('initBtn:function(t){var e=t.state',
         'initBtn:function(t){if(0===t.state&&Number(t.registerStartTime)>Date.now()){uni.__ljSetupWatchButton(this,t);return}var e=t.state'),
        ('initBtns:function(){this.showBtn=!0;var t=this.activitieItem',
         'initBtns:function(){this.showBtn=!0;if(0===this.activitieItem.state&&Number(this.activitieItem.startRegisterTime)>Date.now()){this.isSign=!1;uni.__ljSetupWatchButton(this,this.activitieItem);return}var t=this.activitieItem'),
        ('t._$s(20,"i",0!==t.activitieItem.state)',
         't._$s(20,"i",0!==t.activitieItem.state||88===t.btnState||89===t.btnState)'),
    ]
    for before, after in patches:
        if text.count(before) != 1:
            raise ValueError('Activity UI changed: ' + before)
        text = text.replace(before, after)
    before = 'handelClick:function(){var t=this;return l(i.default.mark('
    if text.count(before) != 2:
        raise ValueError('Expected exactly list and detail activity click handlers')
    text = text.replace(before,
        'handelClick:function(){if(88===this.btnState||89===this.btnState){if(this.disabled)return;return uni.__ljWatchRegistration(this.detailData||this.activitieItem,89===this.btnState)}var t=this;return l(i.default.mark(')
    for before, after in [
        ('btnStyleFn:function(){this.disabled?', 'btnStyleFn:function(){if(89===this.btnState){this.btnStyle.backgroundColor="#F5F6FA";this.btnStyle.color="#222";return}this.disabled?'),
        ('setBtnStyle:function(){this.bgColor=', 'setBtnStyle:function(){if(89===this.btnState){this.bgColor="#F5F6FA";this.color="#222";return}this.bgColor=')]:
        if text.count(before) != 1:
            raise ValueError('Unexpected button style function')
        text=text.replace(before,after)
    return text


def patch_view(text):
    marker = '.item-content .text[data-v-cad81edc]'
    if text.count(marker) != 1:
        raise ValueError('Unexpected activity card stylesheet')
    css = (
        '.item-content-right .activitie-content[data-v-cad81edc]{display:grid!important;grid-template-columns:minmax(0,1fr) auto;column-gap:12rpx;row-gap:6rpx}'
        '.item-content-right .activitie-content-left[data-v-cad81edc]{display:contents}'
        '.item-content-right .activitie-content-left .dowm-date[data-v-cad81edc]{grid-column:1 / -1;grid-row:1;white-space:nowrap;height:auto;min-height:36rpx;width:max-content;max-width:100%}'
        '.dowm-date[data-v-cad81edc]>*{flex-shrink:0;white-space:nowrap}'
        '.item-content-right .activitie-content-left .bm[data-v-cad81edc]{grid-column:1;grid-row:2;align-self:center}'
        '.item-content-right .activitie-content-right[data-v-cad81edc]{grid-column:2;grid-row:2;justify-self:end;white-space:nowrap}'
    )
    return text.replace(marker,css+marker)
