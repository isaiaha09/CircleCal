(function(){
    // Admin JS to dynamically update the subscription chooser based on selected DiscountCode
    function $(sel, ctx){ return (ctx||document).querySelector(sel); }
    function $all(sel, ctx){ return Array.prototype.slice.call((ctx||document).querySelectorAll(sel)); }
    function buildRadioHtml(name, idPrefix, choices, currentVal){
        // kept for backward compatibility but we prefer selects now
        return choices.map(function(c){
            var checked = (String(c.id) === String(currentVal)) ? ' checked' : '';
            var inputId = idPrefix + '_' + c.id;
            return '<div class="admin-radio-choice">'
                 + '<input type="radio" name="' + name + '" id="' + inputId + '" value="' + c.id + '"' + checked + '> '
                 + '<label for="' + inputId + '">' + (c.label || c.id) + '</label>'
                 + '</div>';
        }).join('');
    }

    function buildSelectHtml(name, id, choices, currentVal, includePlaceholder, placeholderText){
        var opts = choices.map(function(c){
            var sel = (String(c.id) === String(currentVal)) ? ' selected' : '';
            return '<option value="' + c.id + '"' + sel + '>' + (c.label || c.id) + '</option>';
        }).join('');
        var placeholder = '';
        if(includePlaceholder){
            var text = placeholderText || '-- Select subscription --';
            placeholder = '<option value="" disabled' + (currentVal ? '' : ' selected') + '>' + text + '</option>';
        }
        return '<select name="' + name + '" id="' + id + '" class="vLargeTextField">' + placeholder + opts + '</select>';
    }

    function findFieldRow(fieldName){
        return document.querySelector('.form-row.field-' + fieldName);
    }

    // Try to find the element we can safely write into for a given field name.
    // Admin markup varies between Django versions and custom templates; be defensive.
    function findFieldBox(fieldName){
        // 1) Preferred: .form-row.field-<name> .field-box
        var row = document.querySelector('.form-row.field-' + fieldName);
        if(row){
            var box = row.querySelector('.field-box');
            if(box) return box;
            return row; // fallback to the row itself
        }

        // 2) Try to find an input/select with the name and climb to closest form-row
        var el = document.querySelector('#id_' + fieldName + ', select[name="' + fieldName + '"] , input[name="' + fieldName + '"]');
        if(el){
            var parentRow = el.closest('.form-row');
            if(parentRow){
                var box2 = parentRow.querySelector('.field-box');
                if(box2) return box2;
                return parentRow;
            }
            // fallback: return the element's parent
            return el.parentElement || el;
        }

        // 3) Try to find a label containing the field name
        var labels = Array.prototype.slice.call(document.querySelectorAll('label'));
        for(var i=0;i<labels.length;i++){
            var t = labels[i].textContent || '';
            if(t.toLowerCase().indexOf(fieldName.toLowerCase()) !== -1){
                var p = labels[i].closest('.form-row');
                if(p){
                    var b = p.querySelector('.field-box');
                    if(b) return b;
                    return p;
                }
            }
        }

        return null;
    }

    function currentSubscriptionValue(){
        var checked = document.querySelector('input[name="subscription"]:checked');
        if(checked) return checked.value;
        var sel = document.querySelector('select[name="subscription"]');
        if(sel) return sel.value;
        return null;
    }

    function refreshSubscriptionsForCode(codeId){
        if(!codeId){
            // nothing selected -> render an empty select with a placeholder so it's always a dropdown
            var boxEmpty = findFieldBox('subscription');
            if(boxEmpty){ boxEmpty.innerHTML = buildSelectHtml('subscription', 'id_subscription', [], null, true); }
            else { console.warn('[admin_discount_dynamic] subscription field box not found to render empty select'); }
            return;
        }
        // Use a predictable admin endpoint path. This assumes the admin is mounted at /admin/.
        // If your admin is mounted elsewhere, update this path accordingly.
        var url = '/admin/billing/applieddiscount/subscriptions-for-code/?code_id=' + encodeURIComponent(codeId);
        fetch(url, {credentials: 'same-origin'}).then(function(resp){
            return resp.json();
        }).then(function(json){
            var data = (json && json.data) || [];
            var box = findFieldBox('subscription');
            if(!box){ console.error('[admin_discount_dynamic] subscription field box not found; aborting render'); return; }
            var cur = currentSubscriptionValue();
            if(data.length === 0){
                // Render a select with a single disabled placeholder so the dropdown isn't empty
                box.innerHTML = buildSelectHtml('subscription', 'id_subscription', [], null, true, '-- No subscriptions found --');
                return;
            }
            // Always render a dropdown (select) per request
            var html = buildSelectHtml('subscription', 'id_subscription', data, cur, true);
            box.innerHTML = html;
        }).catch(function(err){
            console.error('Failed fetching subscriptions-for-code', err);
        });
    }

    function init(){
        // Wait until DOM is ready (admin loads jquery.init.js which ensures jQuery, but we avoid depending on jQuery here)
        var discountSelect = document.getElementById('id_discount_code');
        if(!discountSelect) {
            // try name lookup as fallback
            discountSelect = document.querySelector('select[name="discount_code"]');
        }
        if(!discountSelect) return;

        // On page load, trigger populate for current value (useful when editing existing obj)
        refreshSubscriptionsForCode(discountSelect.value);

        discountSelect.addEventListener('change', function(e){
            var val = e.target.value;
            refreshSubscriptionsForCode(val);
        });
    }

    // Run on DOMContentLoaded
    if(document.readyState === 'loading'){
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
