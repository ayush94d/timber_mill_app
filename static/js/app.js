(function(){
  const $ = id => document.getElementById(id);
  const WOOD_LABELS = { teak:'Teak', sal:'Sal', bija:'Bija', khamhar:'Khamhar', others:'Others' };

  // ---------------------------------------------------------------------
  // Shared helpers
  // ---------------------------------------------------------------------
  async function api(path, options){
    const res = await fetch('/api' + path, Object.assign({
      headers: {'Content-Type':'application/json'}
    }, options));
    if(!res.ok){
      const err = await res.json().catch(()=>({error:'Request failed'}));
      throw new Error(err.error || 'Request failed');
    }
    return res.json();
  }
  function fmtMoney(n){
    return '₹' + (n||0).toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2});
  }
  let toastTimer;
  function showToast(msg){
    const t = $('toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(()=> t.classList.remove('show'), 1800);
  }

  // ---------------------------------------------------------------------
  // Bottom nav / tabs
  // ---------------------------------------------------------------------
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const tab = btn.dataset.tab;
      $('tab-estimate').style.display = tab === 'estimate' ? 'block' : 'none';
      $('tab-payroll').style.display = tab === 'payroll' ? 'block' : 'none';
      if(tab === 'payroll') loadEmployees();
    });
  });

  // =======================================================================
  // ESTIMATE / CHALLAN
  // =======================================================================
  let rates = {};
  let items = [];
  let customer = { name:'', phone:'' };
  let makingCharges = 0;
  let currentEstimate = null; // server response after POST /api/estimates
  let advancePaid = 0;
  let deliveryDate = '';
  let ownerEmail = localStorage.getItem('asm:ownerEmail') || '';

  function toFeet(ft, inch){ return (parseFloat(ft)||0) + (parseFloat(inch)||0)/12; }
  function toInches(ft, inch){ return (parseFloat(ft)||0)*12 + (parseFloat(inch)||0); }
  function fmtDims(it){
    const f = (ft,inn) => {
      let s = '';
      if(ft) s += ft + "'";
      if(inn) s += (s?' ':'') + inn + '"';
      return s || '0';
    };
    return f(it.len_ft, it.len_in) + ' × ' + f(it.wid_ft, it.wid_in) + ' × ' + f(it.hgt_ft, it.hgt_in);
  }
  function computeCft(it){
    return toFeet(it.len_ft, it.len_in) * toFeet(it.wid_ft, it.wid_in) * toFeet(it.hgt_ft, it.hgt_in);
  }
  function withinStandardSize(lenFt,lenIn,hgtFt,hgtIn){
    return toFeet(lenFt,lenIn) <= 7 && toInches(hgtFt,hgtIn) <= 4;
  }
  function itemsSubtotal(){ return items.reduce((s,it)=> s + computeCft(it)*it.qty*it.rate, 0); }
  function itemsTotalCft(){ return items.reduce((s,it)=> s + computeCft(it)*it.qty, 0); }
  function grandTotal(){ return itemsSubtotal() + (makingCharges||0); }

  function setStep(n){
    document.querySelectorAll('.step').forEach(el => {
      const s = parseInt(el.dataset.step);
      el.classList.toggle('active', s === n);
      el.classList.toggle('done', s < n);
    });
  }

  function showGate(prefill){
    $('mainContent').style.display = 'none';
    $('challanView').style.display = 'none';
    $('gatePanel').style.display = 'block';
    setStep(1);
    if(prefill){
      $('gateName').value = customer.name || '';
      $('gatePhone').value = customer.phone || '';
    }
    validatePhone();
  }
  function showMain(){
    $('gatePanel').style.display = 'none';
    $('challanView').style.display = 'none';
    $('mainContent').style.display = 'block';
    setStep(2);
    $('stripName').textContent = customer.name || 'Walk-in customer';
    $('stripPhone').textContent = customer.phone;
  }
  function showChallanView(){
    $('gatePanel').style.display = 'none';
    $('mainContent').style.display = 'none';
    $('challanView').style.display = 'block';
    setStep(3);
    renderChallan();
    window.scrollTo(0,0);
  }

  function validatePhone(){
    const input = $('gatePhone');
    const hint = $('gatePhoneHint');
    const digits = input.value.replace(/\D/g,'').slice(0,10);
    if(input.value !== digits) input.value = digits;
    const isValid = /^\d{10}$/.test(digits);
    input.classList.toggle('valid', isValid);
    input.classList.toggle('invalid', digits.length > 0 && !isValid);
    if(digits.length === 0){
      hint.textContent = 'Enter 10 digits, e.g. 9876543210';
      hint.className = 'field-hint';
    } else if(isValid){
      hint.textContent = '✓ Valid mobile number';
      hint.className = 'field-hint valid';
    } else {
      hint.textContent = digits.length + '/10 digits entered';
      hint.className = 'field-hint invalid';
    }
    $('gateBeginBtn').disabled = !isValid;
    return isValid;
  }
  $('gatePhone').addEventListener('input', validatePhone);

  $('gateBeginBtn').addEventListener('click', () => {
    if(!validatePhone()) return;
    customer = { name: $('gateName').value.trim(), phone: $('gatePhone').value.trim() };
    showMain();
  });
  $('changeCustomerBtn').addEventListener('click', () => {
    items = [];
    currentEstimate = null;
    renderLedger();
    showGate(true);
  });
  $('backToEstimateBtn').addEventListener('click', showMain);

  // ---- Rates ----
  async function loadRates(){
    rates = await api('/rates');
    renderRatesGrid();
  }
  function renderRatesGrid(){
    const grid = $('ratesGrid');
    grid.innerHTML = Object.keys(WOOD_LABELS).map(key => {
      const val = rates[key];
      return '<div class="rate-field"><label>' + WOOD_LABELS[key] + '</label>' +
        '<div class="rate-input-group"><span>₹</span>' +
        '<input type="number" min="0" step="1" data-wood="' + key + '" class="default-rate-input" value="' + (val!=null?val:'') + '"></div></div>';
    }).join('');
    grid.querySelectorAll('.default-rate-input').forEach(inp => {
      inp.addEventListener('change', async () => {
        const wood = inp.dataset.wood;
        const rate = parseFloat(inp.value) || 0;
        try{
          await api('/rates/' + wood, { method:'PUT', body: JSON.stringify({rate}) });
          rates[wood] = rate;
          updateRateStatus();
          showToast(WOOD_LABELS[wood] + ' rate updated');
        }catch(e){ showToast('Could not save rate'); }
      });
    });
  }

  // ---- Add piece ----
  function updateRateStatus(){
    const wood = $('woodType').value;
    const lenFt=$('lenFt').value, lenIn=$('lenIn').value, widFt=$('widFt').value, widIn=$('widIn').value, hgtFt=$('hgtFt').value, hgtIn=$('hgtIn').value;
    const hasDims = [lenFt,lenIn,widFt,widIn,hgtFt,hgtIn].some(v => parseFloat(v) > 0);
    const standard = withinStandardSize(lenFt,lenIn,hgtFt,hgtIn);
    const defaultRate = rates[wood];
    const box = $('rateStatus'), text = $('rateStatusText'), rateInput = $('itemRate');
    if(!hasDims){ box.classList.remove('warn'); text.textContent = 'Enter dimensions to check the rate'; return; }
    if(standard && defaultRate){
      box.classList.remove('warn');
      text.textContent = '✓ ' + WOOD_LABELS[wood] + ' — standard size, default rate applied (editable)';
      if(rateInput.dataset.auto !== 'false'){ rateInput.value = defaultRate; rateInput.dataset.auto = 'true'; }
    } else {
      box.classList.add('warn');
      text.textContent = '⚠ Outside standard size — enter your rate for this piece';
      if(rateInput.dataset.auto !== 'false'){ rateInput.value = ''; }
    }
  }
  $('itemRate').addEventListener('input', () => { $('itemRate').dataset.auto = 'false'; updateLiveCalc(); });
  ['woodType','lenFt','lenIn','widFt','widIn','hgtFt','hgtIn'].forEach(id => {
    $(id).addEventListener('input', () => { $('itemRate').dataset.auto = 'true'; updateRateStatus(); updateLiveCalc(); });
  });
  $('woodType').addEventListener('change', () => { $('itemRate').dataset.auto = 'true'; updateRateStatus(); updateLiveCalc(); });

  function updateLiveCalc(){
    const it = { len_ft:$('lenFt').value, len_in:$('lenIn').value, wid_ft:$('widFt').value, wid_in:$('widIn').value, hgt_ft:$('hgtFt').value, hgt_in:$('hgtIn').value };
    const cft = computeCft(it);
    const qty = parseFloat($('itemQty').value) || 1;
    const rate = parseFloat($('itemRate').value) || 0;
    const totalCft = cft * qty;
    $('liveCft').textContent = totalCft.toFixed(3) + ' CFT × ' + qty + ' pc';
    $('liveCost').textContent = fmtMoney(totalCft * rate);
    $('addBtn').disabled = !(cft > 0 && rate > 0);
  }
  $('itemQty').addEventListener('input', updateLiveCalc);

  $('makingChargesInput').addEventListener('input', () => {
    makingCharges = parseFloat($('makingChargesInput').value) || 0;
    renderLedger();
  });

  $('addBtn').addEventListener('click', () => {
    const it = { len_ft:$('lenFt').value, len_in:$('lenIn').value, wid_ft:$('widFt').value, wid_in:$('widIn').value, hgt_ft:$('hgtFt').value, hgt_in:$('hgtIn').value };
    const cft = computeCft(it);
    const rate = parseFloat($('itemRate').value) || 0;
    if(cft <= 0 || rate <= 0){ updateRateStatus(); return; }
    items.push({
      name: $('itemName').value.trim(), wood: $('woodType').value, rate, qty: parseInt($('itemQty').value)||1,
      len_ft: parseFloat($('lenFt').value)||0, len_in: parseFloat($('lenIn').value)||0,
      wid_ft: parseFloat($('widFt').value)||0, wid_in: parseFloat($('widIn').value)||0,
      hgt_ft: parseFloat($('hgtFt').value)||0, hgt_in: parseFloat($('hgtIn').value)||0
    });
    ['itemName','lenFt','lenIn','widFt','widIn','hgtFt','hgtIn'].forEach(id => $(id).value = '');
    $('itemQty').value = 1; $('itemRate').value = ''; $('itemRate').dataset.auto = 'true';
    updateRateStatus(); updateLiveCalc(); renderLedger();
    showToast('Added to estimate');
  });

  function renderLedger(){
    const area = $('ledgerArea');
    if(items.length === 0){
      area.innerHTML = '<div class="ledger-empty">No pieces added yet. Fill in the form above and add your first piece.</div>';
      return;
    }
    let rows = '';
    items.forEach((it, idx) => {
      const cft = computeCft(it) * it.qty;
      rows += '<tr><td class="name">' + (it.name||'Item') + '</td><td class="wood">' + WOOD_LABELS[it.wood] + '</td>' +
        '<td class="dims">' + fmtDims(it) + '</td><td class="num">' + it.qty + '</td><td class="num">' + cft.toFixed(3) + '</td>' +
        '<td class="num">' + fmtMoney(it.rate) + '</td><td class="num">' + fmtMoney(cft*it.rate) + '</td>' +
        '<td><button class="remove-btn" data-idx="' + idx + '">×</button></td></tr>';
    });
    area.innerHTML =
      '<div class="table-scroll"><table><thead><tr><th>Description</th><th>Wood</th><th>Dimensions</th><th class="num">Qty</th><th class="num">CFT</th><th class="num">Rate</th><th class="num">Cost</th><th></th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
      '<div class="totals-breakdown"><div class="totals-line"><span>Items subtotal</span><b>' + fmtMoney(itemsSubtotal()) + '</b></div>' +
      '<div class="totals-line"><span>Making charges</span><b>' + fmtMoney(makingCharges||0) + '</b></div></div>' +
      '<div class="totals"><div class="cft">Total volume<br><b style="color:var(--ink);font-size:15px;">' + itemsTotalCft().toFixed(3) + ' CFT</b></div>' +
      '<div class="price-tag"><div class="label">Grand Total</div><div class="amount">' + fmtMoney(grandTotal()) + '</div></div></div>' +
      '<div class="action-row"><button class="btn-whatsapp" id="shareBtn">Share via WhatsApp</button>' +
      '<button class="btn-confirm" id="confirmOrderBtn">Confirm Order &rarr;</button></div>' +
      '<div class="clear-row"><button id="clearAllBtn">Clear entire estimate</button></div>';

    area.querySelectorAll('.remove-btn').forEach(btn => btn.addEventListener('click', () => {
      items.splice(parseInt(btn.dataset.idx), 1);
      renderLedger();
      showToast('Item removed');
    }));
    $('clearAllBtn').addEventListener('click', () => { items = []; currentEstimate = null; renderLedger(); showToast('Estimate cleared'); });
    $('shareBtn').addEventListener('click', shareOnWhatsApp);
    $('confirmOrderBtn').addEventListener('click', confirmOrder);
  }

  function buildEstimateText(){
    const dateStr = new Date().toLocaleDateString('en-IN', {day:'numeric', month:'short', year:'numeric'});
    let lines = items.map((it, i) => (i+1) + '. ' + (it.name||'Item') + ' (' + WOOD_LABELS[it.wood] + ')\n   ' +
      fmtDims(it) + '  x' + it.qty + '  =  ' + (computeCft(it)*it.qty).toFixed(3) + ' CFT @ ' + fmtMoney(it.rate) + '/CFT  =  ' + fmtMoney(computeCft(it)*it.qty*it.rate));
    return 'Customer: ' + customer.name + '\nContact: ' + customer.phone + '\nDate: ' + dateStr + '\n\n' +
      lines.join('\n\n') + '\n\n-----------------------------\nTotal Volume: ' + itemsTotalCft().toFixed(3) + ' CFT\n' +
      'Items Subtotal: ' + fmtMoney(itemsSubtotal()) + '\nMaking Charges: ' + fmtMoney(makingCharges||0) + '\nGrand Total: ' + fmtMoney(grandTotal());
  }
  function shareOnWhatsApp(){
    if(!items.length) return;
    const msg = '*Awadh Saw Mill — Estimate*\n' + buildEstimateText();
    window.open('https://wa.me/?text=' + encodeURIComponent(msg), '_blank');
  }

  async function confirmOrder(){
    if(!items.length) return;
    try{
      currentEstimate = await api('/estimates', { method:'POST', body: JSON.stringify({
        customer, items, making_charges: makingCharges
      })});
      showChallanView();
    }catch(e){ showToast(e.message || 'Could not create estimate'); }
  }

  function renderChallan(){
    if(!currentEstimate) return;
    const dateStr = new Date().toLocaleDateString('en-IN', {day:'numeric', month:'short', year:'numeric'});
    $('challanMeta').textContent = (currentEstimate.challan ? currentEstimate.challan.challan_number : 'Not yet confirmed') + '  •  ' + dateStr;
    $('challanCustomerName').textContent = currentEstimate.customer.name || 'Walk-in customer';
    $('challanCustomerPhone').textContent = currentEstimate.customer.phone;

    let rows = '<thead><tr><th>Item</th><th>Wood</th><th>Dims</th><th class="num">Qty</th><th class="num">CFT</th><th class="num">Cost</th></tr></thead><tbody>';
    currentEstimate.items.forEach(it => {
      rows += '<tr><td class="name">' + (it.name||'Item') + '</td><td class="wood">' + WOOD_LABELS[it.wood] + '</td>' +
        '<td class="dims">' + fmtDims(it) + '</td><td class="num">' + it.qty + '</td><td class="num">' + it.cft.toFixed(3) + '</td><td class="num">' + fmtMoney(it.cost) + '</td></tr>';
    });
    $('challanTable').innerHTML = rows + '</tbody>';

    $('challanBreakdown').innerHTML =
      '<div class="totals-line"><span>Items subtotal</span><b>' + fmtMoney(currentEstimate.subtotal) + '</b></div>' +
      '<div class="totals-line"><span>Making charges</span><b>' + fmtMoney(currentEstimate.making_charges) + '</b></div>' +
      '<div class="totals-line"><span>Grand Total</span><b>' + fmtMoney(currentEstimate.grand_total) + '</b></div>';

    advancePaid = currentEstimate.challan ? currentEstimate.challan.advance_paid : 0;
    deliveryDate = currentEstimate.challan ? (currentEstimate.challan.delivery_date || '') : '';
    $('advancePaid').value = advancePaid || 0;
    $('deliveryDate').value = deliveryDate;
    $('challanOwnerEmail').value = ownerEmail;
    updateBalance();
  }
  function updateBalance(){
    const balance = (currentEstimate ? currentEstimate.grand_total : 0) - (advancePaid || 0);
    const el = $('balanceValue');
    el.textContent = fmtMoney(balance);
    el.classList.toggle('negative', balance < 0);
  }
  $('advancePaid').addEventListener('input', () => { advancePaid = parseFloat($('advancePaid').value) || 0; updateBalance(); });
  $('advancePaid').addEventListener('change', saveChallan);
  $('deliveryDate').addEventListener('change', () => { deliveryDate = $('deliveryDate').value; saveChallan(); });
  $('challanOwnerEmail').addEventListener('change', () => {
    ownerEmail = $('challanOwnerEmail').value.trim();
    localStorage.setItem('asm:ownerEmail', ownerEmail);
    $('challanOwnerEmailRow').classList.remove('warn');
  });

  async function saveChallan(){
    if(!currentEstimate) return;
    try{
      currentEstimate = await api('/estimates/' + currentEstimate.id + '/challan', {
        method:'POST', body: JSON.stringify({ advance_paid: advancePaid, delivery_date: deliveryDate })
      });
      $('challanMeta').textContent = currentEstimate.challan.challan_number + '  •  ' + new Date().toLocaleDateString('en-IN', {day:'numeric', month:'short', year:'numeric'});
      showToast('Challan saved');
    }catch(e){ showToast('Could not save challan'); }
  }

  function buildChallanText(){
    if(!currentEstimate) return '';
    const balance = currentEstimate.grand_total - (advancePaid||0);
    let lines = currentEstimate.items.map((it,i) => (i+1) + '. ' + (it.name||'Item') + ' (' + WOOD_LABELS[it.wood] + ') — ' + fmtDims(it) + ' x' + it.qty + ' = ' + it.cft.toFixed(3) + ' CFT = ' + fmtMoney(it.cost));
    return '*Delivery Challan*\n' + (currentEstimate.challan ? currentEstimate.challan.challan_number : '') +
      '\n\nCustomer: ' + currentEstimate.customer.name + '\nContact: ' + currentEstimate.customer.phone + '\n\n' +
      lines.join('\n') + '\n\n-----------------------------\nItems Subtotal: ' + fmtMoney(currentEstimate.subtotal) +
      '\nMaking Charges: ' + fmtMoney(currentEstimate.making_charges) + '\nGrand Total: ' + fmtMoney(currentEstimate.grand_total) +
      '\nAdvance Paid: ' + fmtMoney(advancePaid||0) + '\nBalance Due: ' + fmtMoney(balance) +
      '\nDelivery Date: ' + (deliveryDate ? new Date(deliveryDate).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'}) : 'Not set');
  }
  $('challanShareBtn').addEventListener('click', () => {
    if(!currentEstimate) return;
    window.open('https://wa.me/?text=' + encodeURIComponent(buildChallanText()), '_blank');
  });
  $('challanEmailBtn').addEventListener('click', () => {
    if(!currentEstimate) return;
    if(!ownerEmail){ $('challanOwnerEmailRow').classList.add('warn'); $('challanOwnerEmail').focus(); return; }
    const subject = 'Order Confirmation - ' + (currentEstimate.customer.name || currentEstimate.customer.phone);
    window.location.href = 'mailto:' + encodeURIComponent(ownerEmail) + '?subject=' + encodeURIComponent(subject) + '&body=' + encodeURIComponent(buildChallanText());
  });

  // =======================================================================
  // PAYROLL
  // =======================================================================
  let employees = [];
  let attendanceMap = {}; // employee_id -> status for the selected date

  $('addEmployeeBtn').addEventListener('click', async () => {
    const name = $('empName').value.trim();
    if(!name){ $('empName').focus(); return; }
    try{
      await api('/employees', { method:'POST', body: JSON.stringify({
        name, role: $('empRole').value.trim(), phone: $('empPhone').value.trim(),
        monthly_salary: parseFloat($('empSalary').value) || 0
      })});
      ['empName','empRole','empPhone','empSalary'].forEach(id => $(id).value = '');
      showToast('Employee added');
      loadEmployees();
    }catch(e){ showToast(e.message || 'Could not add employee'); }
  });

  async function loadEmployees(){
    employees = await api('/employees');
    if(!$('attendanceDate').value){
      $('attendanceDate').value = new Date().toISOString().slice(0,10);
    }
    await loadAttendanceForDate();
  }

  async function loadAttendanceForDate(){
    const date = $('attendanceDate').value;
    if(!date || employees.length === 0){
      $('attendanceList').innerHTML = '<div class="ledger-empty">Add an employee to start marking attendance.</div>';
      return;
    }
    const month = date.slice(0,7);
    const records = await api('/attendance?month=' + month);
    attendanceMap = {};
    records.forEach(r => { if(r.date === date) attendanceMap[r.employee_id] = r.status; });
    renderAttendanceList();
  }
  $('loadAttendanceBtn').addEventListener('click', loadAttendanceForDate);

  function renderAttendanceList(){
    const el = $('attendanceList');
    if(employees.length === 0){
      el.innerHTML = '<div class="ledger-empty">No employees yet. Add one above.</div>';
      return;
    }
    el.innerHTML = employees.map(emp => {
      const status = attendanceMap[emp.id] || '';
      return '<div class="employee-row">' +
        '<div><div class="employee-name">' + emp.name + '</div><div class="employee-meta">' + (emp.role||'—') + ' • ' + fmtMoney(emp.monthly_salary) + '/mo</div></div>' +
        '<div class="attendance-btns">' +
          '<button data-id="' + emp.id + '" data-status="present" class="present ' + (status==='present'?'active':'') + '">P</button>' +
          '<button data-id="' + emp.id + '" data-status="half" class="half ' + (status==='half'?'active':'') + '">½</button>' +
          '<button data-id="' + emp.id + '" data-status="absent" class="absent ' + (status==='absent'?'active':'') + '">A</button>' +
        '</div></div>';
    }).join('');
    el.querySelectorAll('.attendance-btns button').forEach(btn => {
      btn.addEventListener('click', async () => {
        const empId = btn.dataset.id, status = btn.dataset.status, date = $('attendanceDate').value;
        try{
          await api('/attendance', { method:'POST', body: JSON.stringify({ employee_id: empId, date, status }) });
          attendanceMap[empId] = status;
          renderAttendanceList();
          showToast('Attendance saved');
        }catch(e){ showToast('Could not save attendance'); }
      });
    });
  }

  $('loadPayrollBtn').addEventListener('click', async () => {
    const month = $('payrollMonth').value;
    if(!month) return;
    const data = await api('/payroll/summary?month=' + month);
    const el = $('payrollSummary');
    if(data.employees.length === 0){
      el.innerHTML = '<div class="ledger-empty">No employees yet.</div>';
      return;
    }
    el.innerHTML = data.employees.map(e =>
      '<div class="payroll-row"><div>' +
        '<div class="name">' + e.name + '</div>' +
        '<div class="sub">' + e.present_days + ' / ' + data.days_in_month + ' days present · ₹' + e.per_day_rate + '/day</div>' +
      '</div><div class="earned">' + fmtMoney(e.earned) + '</div></div>'
    ).join('');
  });

  // ---------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------
  (async function init(){
    $('payrollMonth').value = new Date().toISOString().slice(0,7);
    await loadRates();
    updateRateStatus();
    updateLiveCalc();
    showGate(false);
  })();
})();
